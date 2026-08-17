"""End-to-end lifecycle tests for VotingContract.

Exercises the full lifecycle (deploy -> mixed votes -> deadline/owner end ->
results) and pins the consensus-tolerance and notes-overlap boundaries that the
focused unit tests do not cover directly.
"""

import time

import pytest
from gltest.direct import *


def _to_address(raw):
    """Convert a raw fixture address to the Address type the contract keys by."""
    if isinstance(raw, bytes):
        from genlayer.py.types import Address
        return Address(raw)
    return raw


LEGIT = '{"score": 90, "legitimate": true, "notes": "coherent genuine preference"}'
COERCED = '{"score": 25, "legitimate": false, "notes": "signs of coercion"}'


def test_full_lifecycle_without_owner_action(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie, monkeypatch):
    """Deploy -> mixed votes -> deadline passes -> auto-ended -> correct results.

    No end_voting() call anywhere: the deadline alone must end the poll, and
    vote counts must be hidden from every read path until it does.
    """
    # Alice deploys, so she is the owner
    direct_vm.sender = direct_alice
    contract = direct_deploy("voting.py", "Best programming language?", ["Python", "JavaScript"], duration_seconds=1)

    # Pin the clock mid-period so every vote is deterministic and pre-deadline
    end_timestamp = int(contract.get_voting_status()["end_timestamp"])
    monkeypatch.setattr(time, "time", lambda: float(end_timestamp) - 10.0)

    # Alice: legitimate vote for opt_0
    direct_vm.mock_llm("Analyze this vote", LEGIT)
    direct_vm.sender = direct_alice
    contract.vote_with_reasoning("opt_0", "I prefer Python for its readability")

    # Bob: coerced vote for opt_0 -> recorded, not counted
    direct_vm.clear_mocks()
    direct_vm.mock_llm("Analyze this vote", COERCED)
    direct_vm.sender = direct_bob
    contract.vote_with_reasoning("opt_0", "I was told to vote for Python or else")

    # Charlie: legitimate vote for opt_1
    direct_vm.clear_mocks()
    direct_vm.mock_llm("Analyze this vote", LEGIT)
    direct_vm.sender = direct_charlie
    contract.vote_with_reasoning("opt_1", "JavaScript is essential for the web")

    # While open: status open, tallies/results hidden, options show no counts
    status = contract.get_voting_status()
    assert status["ended"] is False
    assert status["time_remaining"] > 0
    with direct_vm.expect_revert("[EXPECTED] Voting not ended"):
        contract.get_current_tallies()
    with direct_vm.expect_revert("[EXPECTED] Voting not ended"):
        contract.get_results()
    options_open = contract.get_options()
    assert "votes" not in options_open["opt_0"]
    assert "votes" not in options_open["opt_1"]
    assert options_open["opt_0"]["description"] == "Python"

    # Deadline passes -> auto-ended without any owner call
    monkeypatch.setattr(time, "time", lambda: float(end_timestamp) + 100.0)
    assert contract.get_voting_status()["ended"] is True

    # Votes past the deadline are rejected
    with direct_vm.expect_revert("[EXPECTED] Voting period has ended"):
        contract.vote_with_reasoning("opt_1", "Too late")

    # Tallies and results are now readable and consistent
    tallies = contract.get_current_tallies()
    results = contract.get_results()
    assert tallies == results
    assert tallies["opt_0"]["votes"] == 1  # only Alice's legitimate vote
    assert tallies["opt_1"]["votes"] == 1  # Charlie's
    assert tallies["_total"] == 2

    # Options now expose counts
    options_closed = contract.get_options()
    assert options_closed["opt_0"]["votes"] == 1
    assert options_closed["opt_1"]["votes"] == 1

    # Full audit trail is consistent
    records = contract.get_all_vote_records()
    assert len(records) == 3
    by_voter = {rec["voter"]: rec for rec in records.values()}
    alice_rec = by_voter[str(_to_address(direct_alice))]
    assert alice_rec["is_legitimate"] is True
    assert alice_rec["legitimacy_score"] >= 70
    assert alice_rec["option_id"] == "opt_0"
    bob_rec = by_voter[str(_to_address(direct_bob))]
    assert bob_rec["is_legitimate"] is False
    assert bob_rec["legitimacy_score"] < 70
    charlie_rec = by_voter[str(_to_address(direct_charlie))]
    assert charlie_rec["is_legitimate"] is True
    assert charlie_rec["option_id"] == "opt_1"


def test_end_voting_by_owner_after_deadline(direct_vm, direct_deploy, direct_alice, direct_bob, monkeypatch):
    """Owner ends the poll after the deadline; non-owners cannot."""
    # Alice deploys, so she is the owner
    direct_vm.sender = direct_alice
    contract = direct_deploy("voting.py", "Q?", ["A", "B"], duration_seconds=1)

    end_timestamp = int(contract.get_voting_status()["end_timestamp"])
    monkeypatch.setattr(time, "time", lambda: float(end_timestamp) + 100.0)

    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Only owner"):
        contract.end_voting()

    direct_vm.sender = direct_alice
    contract.end_voting()
    assert contract.get_voting_status()["ended"] is True
    assert contract.get_results()["_total"] == 0


def test_empty_reasoning_rejected(direct_vm, direct_deploy, direct_alice):
    """Whitespace-only reasoning is rejected up front, not sent to the LLM."""
    direct_vm.sender = direct_alice
    contract = direct_deploy("voting.py", "Q?", ["A", "B"])
    with direct_vm.expect_revert("[EXPECTED] Reasoning cannot be empty"):
        contract.vote_with_reasoning("opt_0", "   ")
    with direct_vm.expect_revert("[EXPECTED] Reasoning cannot be empty"):
        contract.vote_with_reasoning("opt_0", "")


def test_empty_option_text_rejected(direct_vm, direct_deploy):
    """Options must have non-empty text at deploy time."""
    with direct_vm.expect_revert("[EXPECTED] Option text cannot be empty"):
        direct_deploy("voting.py", "Q?", ["Yes", "   "])


def test_validator_score_tolerance_boundary(direct_vm, direct_deploy, direct_alice):
    """Leader/validator scores may differ by at most 15 points."""
    direct_vm.sender = direct_alice
    contract = direct_deploy("voting.py", "Q?", ["A", "B"])

    NOTES = "coherent genuine preference"

    # Cast a vote first so the consensus validator is captured for run_validator
    direct_vm.mock_llm(
        "Analyze this vote",
        '{"score": 85, "legitimate": true, "notes": "%s"}' % NOTES,
    )
    contract.vote_with_reasoning("opt_0", "a vote to capture the validator")

    # Exactly 15 apart -> accepted
    direct_vm.clear_mocks()
    direct_vm.mock_llm(
        "Analyze this vote",
        '{"score": 70, "legitimate": true, "notes": "%s"}' % NOTES,
    )
    assert direct_vm.run_validator(
        leader_result={"score": 85, "legitimate": True, "info": NOTES}
    ) is True

    # 16 apart -> rejected
    direct_vm.clear_mocks()
    direct_vm.mock_llm(
        "Analyze this vote",
        '{"score": 69, "legitimate": true, "notes": "%s"}' % NOTES,
    )
    assert direct_vm.run_validator(
        leader_result={"score": 85, "legitimate": True, "info": NOTES}
    ) is False


def test_validator_notes_overlap_boundary(direct_vm, direct_deploy, direct_alice):
    """Explanations must share at least 30% of their combined vocabulary."""
    direct_vm.sender = direct_alice
    contract = direct_deploy("voting.py", "Q?", ["A", "B"])

    L_NOTES = "coherent genuine preference"  # 3 words

    # Cast a vote first so the consensus validator is captured for run_validator
    direct_vm.mock_llm(
        "Analyze this vote",
        '{"score": 85, "legitimate": true, "notes": "%s"}' % L_NOTES,
    )
    contract.vote_with_reasoning("opt_0", "a vote to capture the validator")

    # Overlap 3/7 = ~43% -> accepted
    direct_vm.clear_mocks()
    direct_vm.mock_llm(
        "Analyze this vote",
        '{"score": 85, "legitimate": true, "notes": "coherent genuine preference for the chosen option"}',
    )
    assert direct_vm.run_validator(
        leader_result={"score": 85, "legitimate": True, "info": L_NOTES}
    ) is True

    # Zero overlap -> rejected
    direct_vm.clear_mocks()
    direct_vm.mock_llm(
        "Analyze this vote",
        '{"score": 85, "legitimate": true, "notes": "totally unrelated assessment"}',
    )
    assert direct_vm.run_validator(
        leader_result={"score": 85, "legitimate": True, "info": L_NOTES}
    ) is False


def test_vote_record_timestamps_within_voting_period(direct_vm, direct_deploy, direct_alice, direct_bob, monkeypatch):
    """Recorded vote timestamps must fall strictly before the deadline."""
    direct_vm.sender = direct_alice
    contract = direct_deploy("voting.py", "Q?", ["A", "B"], duration_seconds=1)
    end_timestamp = int(contract.get_voting_status()["end_timestamp"])

    # Pin the clock mid-period so the recorded timestamps are deterministic
    monkeypatch.setattr(time, "time", lambda: float(end_timestamp) - 1.0)
    direct_vm.mock_llm("Analyze this vote", LEGIT)

    direct_vm.sender = direct_alice
    contract.vote_with_reasoning("opt_0", "early vote")
    direct_vm.sender = direct_bob
    contract.vote_with_reasoning("opt_1", "another early vote")

    for rec in contract.get_all_vote_records().values():
        assert int(rec["timestamp"]) == end_timestamp - 1
        assert int(rec["timestamp"]) < end_timestamp


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
