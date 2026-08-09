# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
from dataclasses import dataclass
import json

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


class VotingContract(gl.Contract):
    # Contract storage
    owner: Address
    question: str
    options: TreeMap[str, VoteOption]
    voters: TreeMap[str, Address]  # voter_address -> option_id they voted for
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
    def vote(self, voter_addr: Address, option_id: str):
        # Check if voting has ended
        if self.voting_ended:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Voting has ended")

        # Check if option exists
        if option_id not in self.options:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Invalid option")

        # Check if voter already voted
        if voter_addr in self.voters:
            raise gl.vm.UserError(f"{ERR_EXPECTED} Already voted")

        # Record vote
        self.voters[voter_addr] = option_id
        opt = self.options[option_id]
        opt.votes += 1

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
        total_votes = u256(0)

        for opt_id, opt in self.options.items():
            results[opt_id] = {
                "description": opt.description,
                "votes": opt.votes
            }
            total_votes += opt.votes

        results["_total"] = total_votes
        return results

    @gl.public.view
    def get_question(self) -> str:
        return self.question

    @gl.public.view
    def has_voted(self, voter_addr: Address) -> bool:
        return voter_addr in self.voters

    @gl.public.view
    def get_voting_status(self) -> dict:
        return {
            "ended": self.voting_ended,
            "current_block": gl.get_block_number(),
            "end_block": self.end_block
        }