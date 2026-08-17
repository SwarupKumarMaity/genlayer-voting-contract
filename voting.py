# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
from dataclasses import dataclass
import re
import time

# Error classifications
ERR_EXPECTED = "[EXPECTED]"
ERR_EXTERNAL = "[EXTERNAL]"
ERR_TRANSIENT = "[TRANSIENT]"
ERR_LLM = "[LLM_ERROR]"

# A vote counts iff its normalized legitimacy score reaches this threshold.
# This is the single source of truth: the boolean flag is always derived from
# the score, never taken from the model's own `legitimate` field.
LEGITIMACY_THRESHOLD = 70


def _normalize_score(raw) -> int:
    """Clamp an arbitrary model-supplied score into 0-100. Unparseable -> 50."""
    try:
        return max(0, min(100, int(round(float(raw)))))
    except:
        return 50


def _is_legitimate(score: int) -> bool:
    """The one and only definition of legitimacy."""
    return int(score) >= LEGITIMACY_THRESHOLD


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
    legitimacy_score: u256  # 0-100
    is_legitimate: bool
    timestamp: str


class VotingContract(gl.Contract):
    owner: Address
    question: str
    options: TreeMap[str, VoteOption]
    voters: TreeMap[str, VoteRecord]  # str(address) -> VoteRecord
    voting_ended: bool
    end_timestamp: u256  # Unix timestamp (seconds)

    def __init__(
        self,
        question: str,
        options: list[str],
        duration_seconds: u256 = 604800  # default = 7 days
    ):
        if not question or not question.strip():
            raise gl.vm.UserError(f"{ERR_EXPECTED} Question cannot be empty")
        if not options:
            raise gl.vm.UserError(f"{ERR_EXPECTED} At least one option required")
        if duration_seconds == 0:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Duration must be > 0")

        self.owner = gl.message.sender_address
        self.question = question.strip()
        self.voting_ended = False
        self.end_timestamp = u256(int(time.time()) + int(duration_seconds))

        # Storage TreeMaps are already zero-initialized — do NOT assign TreeMap()
        for i, opt_text in enumerate(options):
            if not opt_text or not opt_text.strip():
                raise gl.vm.UserError(f"{ERR_EXPECTED} Option text cannot be empty")
            opt_id = f"opt_{i}"
            self.options[opt_id] = VoteOption(
                option_id=opt_id,
                description=opt_text,
                votes=0
            )

    def _voting_is_over(self) -> bool:
        """Voting is over once the owner ends it OR the deadline has passed.

        The deadline is authoritative: even if the owner never calls
        end_voting(), the poll is over as soon as end_timestamp passes.
        """
        return self.voting_ended or u256(int(time.time())) >= self.end_timestamp

    @gl.public.write
    def vote_with_reasoning(self, option_id: str, reasoning: str):
        if self.voting_ended:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Voting has ended")

        # Enforce the configured deadline: no votes are accepted once
        # end_timestamp has passed, even if the owner has not called
        # end_voting() yet.
        if u256(int(time.time())) >= self.end_timestamp:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Voting period has ended")

        if option_id not in self.options:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Invalid option")

        if not reasoning or not reasoning.strip():
            raise gl.vm.UserError(f"{ERR_EXPECTED} Reasoning cannot be empty")

        voter_addr = gl.message.sender_address
        voter_key = str(voter_addr)

        if voter_key in self.voters:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Already voted")

        legitimacy_result = self._check_vote_legitimacy(voter_addr, option_id, reasoning)

        # Re-derive on the write path: what is stored and what is tallied both
        # come from the score, so the record can never contradict the threshold.
        score = _normalize_score(legitimacy_result["score"])
        is_legit = _is_legitimate(score)

        vote_record = VoteRecord(
            voter_addr=voter_addr,
            option_id=option_id,
            reasoning=reasoning,
            legitimacy_score=u256(score),
            is_legitimate=is_legit,
            timestamp=str(int(time.time()))
        )

        self.voters[voter_key] = vote_record

        if is_legit:
            self.options[option_id].votes += 1

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
- notes: brief explanation of your assessment

Only the score matters; the contract decides legitimacy from it (score >= {LEGITIMACY_THRESHOLD})."""

            try:
                resp = gl.nondet.exec_prompt(prompt, response_format="json")
                nt = str(resp.get("notes", ""))[:200]

                # The flag is DERIVED from the normalized score, never read from
                # the model response, so score and flag can never disagree.
                sc = _normalize_score(resp.get("score", 50))
                le = _is_legitimate(sc)

                return {"score": sc, "legitimate": le, "info": nt}
            except Exception as e:
                raise gl.vm.UserError(f"{ERR_LLM} {str(e)}")

        def validator_fn(leader_res: gl.vm.Result) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return self._err_handler(leader_res, leader_fn)

            try:
                v_res = leader_fn()
                l_sc = _normalize_score(leader_res.calldata["score"])
                l_le = bool(leader_res.calldata["legitimate"])
                l_info = leader_res.calldata["info"]
                v_sc = v_res["score"]
                v_le = v_res["legitimate"]
                v_info = v_res["info"]

                # Reject a leader whose flag does not match its own score.
                if l_le != _is_legitimate(l_sc):
                    return False
                # Same rule re-checked on our own result (defensive; derived above).
                if v_le != _is_legitimate(v_sc):
                    return False

                if abs(l_sc - v_sc) > 15:
                    return False
                if l_le != v_le:
                    return False

                if l_info and v_info:
                    l_words = set(re.findall(r'\b\w+\b', l_info.lower()))
                    v_words = set(re.findall(r'\b\w+\b', v_info.lower()))
                    if l_words and v_words:
                        overlap = len(l_words & v_words)
                        total = len(l_words | v_words)
                        if total > 0 and (overlap / total) < 0.3:
                            return False
                elif l_info or v_info:
                    return False

                return True
            except:
                return False

        return gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

    def _err_handler(self, lead_res, lead_fn) -> bool:
        lead_msg = getattr(lead_res, 'message', '')
        try:
            lead_fn()
            return False
        except gl.vm.UserError as e:
            val_msg = getattr(e, 'message', str(e))
            if val_msg.startswith(ERR_EXPECTED) or val_msg.startswith(ERR_EXTERNAL):
                return val_msg == lead_msg
            if val_msg.startswith(ERR_TRANSIENT) and lead_msg.startswith(ERR_TRANSIENT):
                return True
            return False
        except:
            return False

    @gl.public.write
    def end_voting(self):
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Only owner")

        if u256(int(time.time())) < self.end_timestamp:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Voting period not finished yet")

        self.voting_ended = True

    @gl.public.view
    def get_results(self) -> dict:
        if not self._voting_is_over():
            raise gl.vm.UserError(f"{ERR_EXPECTED} Voting not ended")

        results = {}
        total = u256(0)
        for opt_id, opt in self.options.items():
            results[opt_id] = {
                "description": opt.description,
                "votes": opt.votes
            }
            total += opt.votes
        results["_total"] = total
        return results

    @gl.public.view
    def get_current_tallies(self) -> dict:
        # Hidden while voting is open: no results until voting has ended
        # (deadline passed or the owner called end_voting).
        if not self._voting_is_over():
            raise gl.vm.UserError(f"{ERR_EXPECTED} Voting not ended")

        results = {}
        total = u256(0)
        for opt_id, opt in self.options.items():
            results[opt_id] = {
                "description": opt.description,
                "votes": opt.votes
            }
            total += opt.votes
        results["_total"] = total
        return results

    @gl.public.view
    def get_options(self) -> dict:
        # Descriptions are always visible so voters can map option IDs, but
        # live vote counts stay hidden until voting has ended.
        if self._voting_is_over():
            return {
                opt_id: {"description": opt.description, "votes": opt.votes}
                for opt_id, opt in self.options.items()
            }
        return {
            opt_id: {"description": opt.description}
            for opt_id, opt in self.options.items()
        }

    @gl.public.view
    def get_question(self) -> str:
        return self.question

    @gl.public.view
    def has_voted(self, voter_addr: Address) -> bool:
        return str(voter_addr) in self.voters

    @gl.public.view
    def get_vote_record(self, voter_addr: Address) -> dict:
        voter_key = str(voter_addr)
        if voter_key not in self.voters:
            raise gl.vm.UserError(f"{ERR_EXPECTED} No vote record found")

        record = self.voters[voter_key]
        return {
            "voter": str(record.voter_addr),
            "option_id": record.option_id,
            "reasoning": record.reasoning,
            "legitimacy_score": record.legitimacy_score,
            "is_legitimate": record.is_legitimate,
            "timestamp": record.timestamp
        }

    @gl.public.view
    def get_all_vote_records(self) -> dict:
        return {
            key: {
                "voter": str(rec.voter_addr),
                "option_id": rec.option_id,
                "reasoning": rec.reasoning,
                "legitimacy_score": rec.legitimacy_score,
                "is_legitimate": rec.is_legitimate,
                "timestamp": rec.timestamp,
            }
            for key, rec in self.voters.items()
        }

    @gl.public.view
    def get_voting_status(self) -> dict:
        now = u256(int(time.time()))
        return {
            "ended": self._voting_is_over(),
            "current_timestamp": now,
            "end_timestamp": self.end_timestamp,
            "time_remaining": max(0, int(self.end_timestamp) - int(now)),
            "owner": str(self.owner)
        }
