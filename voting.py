# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
from dataclasses import dataclass
import json
import re

# Error classifications
ERR_EXPECTED = "[EXPECTED]"
ERR_EXTERNAL = "[EXTERNAL]"
ERR_TRANSIENT = "[TRANSIENT]"
ERR_LLM = "[LLM_ERROR]"


@dataclass
@allow_storage
class VoteOption:
    option_id: str
    description: str
    votes: u256


@dataclass
@allow_storage
class VoteRecord:
    voter_addr: Address
    option_id: str
    reasoning: str
    legitimacy_score: u256  # 0-100 scale from LLM check
    is_legitimate: bool
    timestamp: str


class VotingContract(gl.Contract):
    # Contract storage
    owner: Address
    question: str
    options: TreeMap[str, VoteOption]
    voters: TreeMap[str, VoteRecord]  # voter_address -> their vote record
    voting_ended: bool
    end_block: u256

    def __init__(self, question: str, options: list[str], duration_blocks: u256 = 10080):
        self.owner = gl.message.sender_address
        self.question = question
        self.voters = TreeMap()
        self.voting_ended = False
        self.end_block = gl.get_block_number() + duration_blocks

        # Initialize options
        self.options = TreeMap()
        for i, opt_text in enumerate(options):
            opt_id = f"opt_{i}"
            self.options[opt_id] = VoteOption(
                option_id=opt_id,
                description=opt_text,
                votes=0
            )

    @gl.public.write
    def vote_with_reasoning(self, voter_addr: Address, option_id: str, reasoning: str):
        # Check if voting has ended
        if self.voting_ended:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Voting has ended")

        # Check if option exists
        if option_id not in self.options:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Invalid option")

        # Check if voter already voted
        if voter_addr in self.voters:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Already voted")

        # Validate the vote reasoning using LLM consensus
        legitimacy_result = self._check_vote_legitimacy(voter_addr, option_id, reasoning)

        # Record the vote
        vote_record = VoteRecord(
            voter_addr=voter_addr,
            option_id=option_id,
            reasoning=reasoning,
            legitimacy_score=legitimacy_result["score"],
            is_legitimate=legitimacy_result["legitimate"],
            timestamp=str(gl.get_block_timestamp())
        )

        self.voters[voter_addr] = vote_record

        # Only count the vote if it's deemed legitimate
        if legitimacy_result["legitimate"]:
            opt = self.options[option_id]
            opt.votes += 1

    def _check_vote_legitimacy(self, voter_addr: Address, option_id: str, reasoning: str) -> dict:
        def leader_fn():
            prompt = f"""Analyze this vote for legitimacy concerns. Consider:
1. Does the reasoning show genuine preference or potential coercion/manipulation?
2. Is the reasoning coherent and relevant to the voting question?
3. Are there signs of vote buying, threats, or improper influence?

Vote details:
- Question: "{self.question}"
- Selected option ID: "{option_id}"
- Voter reasoning: "{reasoning}"
- Voter address: {voter_addr}

Respond as JSON with:
- score: 0-100 (0 = definitely illegitimate/coerced, 100 = completely legitimate)
- legitimate: true/false (true if score >= 70)
- notes: brief explanation of your assessment"""

            try:
                resp = gl.nondet.exec_prompt(prompt, response_format="json")
                sc = resp.get("score", 50)
                le = bool(resp.get("legitimate", False))
                nt = str(resp.get("notes", ""))[:200]

                try:
                    sc = max(0, min(100, int(round(float(sc)))))
                except:
                    sc = 50

                return {"score": sc, "legitimate": le, "info": nt}
            except Exception as e:
                raise gl.vm.UserError(f"{ERR_LLM} {str(e)}")

        def validator_fn(leader_res: gl.vm.Result) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return self._err_handler(leader_res, leader_fn)

            try:
                v_res = leader_fn()
                # Leader result fields: score, legitimate, info
                l_sc = leader_res.calldata["score"]
                l_le = leader_res.calldata["legitimate"]
                l_info = leader_res.calldata["info"]
                # Validator result fields: score, legitimate, info
                v_sc = v_res["score"]
                v_le = v_res["legitimate"]
                v_info = v_res["info"]

                # Compare scores with tolerance (allow small variations)
                if abs(l_sc - v_sc) > 15:
                    return False
                # Legitimacy decision must match exactly
                if l_le != v_le:
                    return False

                # Note similarity check: require at least 30% word overlap
                # Only perform if both have notes
                if l_info and v_info:
                    l_words = set(re.findall(r'\b\w+\b', l_info.lower()))
                    v_words = set(re.findall(r'\b\w+\b', v_info.lower()))
                    if l_words and v_words:
                        overlap = len(l_words & v_words)
                        total = len(l_words | v_words)
                        if total > 0 and (overlap / total) < 0.3:
                            return False
                # If one has notes and the other doesn't, they don't match
                elif l_info or v_info:
                    return False

                return True
            except:
                return False

        return gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

    def _err_handler(self, lead_res, lead_fn) -> bool:
        lead_msg = getattr(lead_res, 'message', '')
        try:
            leader_fn()
            return False
        except gl.vm.UserError as e:
            val_msg = getattr(e, 'message', str(e))
            # Deterministic errors: must match exactly
            if val_msg.startswith(ERR_EXPECTED) or val_msg.startswith(ERR_EXTERNAL):
                return val_msg == lead_msg
            # Transient: agree if both hit transient failure
            if val_msg.startswith(ERR_TRANSIENT) and lead_msg.startswith(ERR_TRANSIENT):
                return True
            # LLM or unknown: disagree — forces consensus retry
            return False
        except:
            return False

    @gl.public.write
    def end_voting(self):
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Only owner")

        if gl.get_block_number() < self.end_block:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Voting not ended yet")

        self.voting_ended = True

    @gl.public.view
    def get_results(self) -> dict:
        if not self.voting_ended:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Voting not ended")

        results = {}
        total_legitimate_votes = u256(0)

        for opt_id, opt in self.options.items():
            results[opt_id] = {
                "description": opt.description,
                "votes": opt.votes
            }
            total_legitimate_votes += opt.votes

        results["_total"] = total_legitimate_votes
        return results

    @gl.public.view
    def get_question(self) -> str:
        return self.question

    @gl.public.view
    def has_voted(self, voter_addr: Address) -> bool:
        return voter_addr in self.voters

    @gl.public.view
    def get_vote_record(self, voter_addr: Address) -> dict:
        if voter_addr not in self.voters:
            raise gl.vm.UserError(f"{ERR_EXPECTED} No vote record found")

        record = self.voters[voter_addr]
        return {
            "voter": record.voter_addr,
            "option_id": record.option_id,
            "reasoning": record.reasoning,
            "legitimacy_score": record.legitimacy_score,
            "is_legitimate": record.is_legitimate,
            "timestamp": record.timestamp
        }

    @gl.public.view
    def get_voting_status(self) -> dict:
        return {
            "ended": self.voting_ended,
            "current_block": gl.get_block_number(),
            "end_block": self.end_block
        }