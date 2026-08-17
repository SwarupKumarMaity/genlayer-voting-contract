"""Direct mode tests for VotingContract"""

import time

import pytest
from gltest.direct import *


def _to_address(raw):
    """Convert a raw fixture address to the Address type the contract keys by.

    The direct_alice/direct_bob/direct_charlie fixtures hand out raw 20-byte
    ``bytes`` because genlayer is not importable at fixture setup time.
    VotingContract stores vote records under ``str(gl.message.sender_address)``,
    i.e. the hex form of an ``Address``, so lookups must pass an ``Address``
    (or an already-converted value) rather than the raw bytes.
    """
    if isinstance(raw, bytes):
        from genlayer.py.types import Address
        return Address(raw)
    return raw


def test_create_voting(direct_vm, direct_deploy, direct_alice):
    """Test creating a voting contract"""
    question = "What is your favorite color?"
    options = ["Red", "Green", "Blue"]
    contract = direct_deploy("voting.py", question, options)

    # Check initial state
    assert contract.get_question() == question
    status = contract.get_voting_status()
    assert status["ended"] == False
    assert status["current_timestamp"] >= 0
    assert status["end_timestamp"] > status["current_timestamp"]

    # Results are not available until voting ends
    with direct_vm.expect_revert("[EXPECTED] Voting not ended"):
        contract.get_results()


def test_vote_with_reasoning_and_get_results(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    """Test voting with reasoning and getting results"""
    question = "Best programming language?"
    options = ["Python", "JavaScript", "Go"]
    # Alice deploys the contract, so she is the owner (owner = deploy-time sender)
    direct_vm.sender = direct_alice
    contract = direct_deploy("voting.py", question, options)

    # Register a deterministic legitimate reply for every vote in this test
    direct_vm.mock_llm(
        "Analyze this vote",
        '{"score": 90, "legitimate": true, "notes": "coherent genuine preference"}',
    )

    # Alice votes for Python with reasoning (using her address as sender)
    direct_vm.sender = direct_alice
    contract.vote_with_reasoning("opt_0", "I prefer Python for its simplicity and readability")  # Python is opt_0

    # Bob votes for JavaScript with reasoning
    direct_vm.sender = direct_bob
    contract.vote_with_reasoning("opt_1", "JavaScript is essential for web development")  # JavaScript is opt_1

    # Charlie votes for Python with reasoning
    direct_vm.sender = direct_charlie
    contract.vote_with_reasoning("opt_0", "Python has great libraries for data science")  # Python is opt_0

    # Check voting status (records are keyed by str(Address), so pass Address)
    assert contract.has_voted(_to_address(direct_alice)) == True
    assert contract.has_voted(_to_address(direct_bob)) == True
    assert contract.has_voted(_to_address(direct_charlie)) == True

    # Try to vote again - should fail (Charlie is still the sender)
    with direct_vm.expect_revert("[EXPECTED] Already voted"):
        contract.vote_with_reasoning("opt_2", "Trying to vote twice")

    # End voting (owner only) - Alice deployed the contract, so she's the owner
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED] Voting period not finished yet"):
        contract.end_voting()


def test_legitimacy_scoring_populates_vote_record(direct_vm, direct_deploy, direct_alice):
    """Every vote must carry a legitimacy_score (0-100) and is_legitimate flag
    produced by the leader/validator consensus check, not left as defaults."""
    question = "Best programming language?"
    options = ["Python", "JavaScript"]
    contract = direct_deploy("voting.py", question, options)

    # Deterministic legitimate reply so the vote record is populated from the
    # LLM consensus path instead of failing with [LLM_ERROR]
    direct_vm.mock_llm(
        "Analyze this vote",
        '{"score": 85, "legitimate": true, "notes": "coherent genuine preference"}',
    )

    direct_vm.sender = direct_alice
    contract.vote_with_reasoning("opt_0", "I prefer Python for its readability and mature ecosystem")

    record = contract.get_vote_record(_to_address(direct_alice))
    assert 0 <= record["legitimacy_score"] <= 100
    assert isinstance(record["is_legitimate"], bool)
    # Contract's own rule: legitimate iff score >= 70 (see leader_fn prompt contract)
    assert record["is_legitimate"] == (record["legitimacy_score"] >= 70)


def test_only_legitimate_votes_are_counted(direct_vm, direct_deploy, direct_alice, direct_bob, monkeypatch):
    """Core consensus invariant: a vote is only added to the tally when the
    leader/validator consensus judged it legitimate (score >= 70)."""
    question = "Best programming language?"
    options = ["Python", "JavaScript"]

    # Phase 1: a legitimate (high-score) vote is counted.
    contract = direct_deploy("voting.py", question, options)
    direct_vm.mock_llm(
        "Analyze this vote",
        '{"score": 90, "legitimate": true, "notes": "coherent genuine preference"}',
    )

    direct_vm.sender = direct_alice
    contract.vote_with_reasoning("opt_0", "I prefer Python for its readability and mature ecosystem")

    record = contract.get_vote_record(_to_address(direct_alice))
    assert record["is_legitimate"] is True

    # Phase 2: a below-threshold vote is recorded but NOT counted.
    direct_vm.clear_mocks()
    direct_vm.mock_llm(
        "Analyze this vote",
        '{"score": 30, "legitimate": false, "notes": "signs of coercion"}',
    )

    direct_vm.sender = direct_bob
    contract.vote_with_reasoning("opt_0", "I was told to vote for Python or else")

    record = contract.get_vote_record(_to_address(direct_bob))
    assert record["is_legitimate"] is False

    # Tallies are hidden while voting is open (no results until it ends)
    with direct_vm.expect_revert("[EXPECTED] Voting not ended"):
        contract.get_current_tallies()

    # Once the deadline passes, only Alice's legitimate vote is counted
    end_timestamp = int(contract.get_voting_status()["end_timestamp"])
    monkeypatch.setattr(time, "time", lambda: end_timestamp + 100)
    tallies = contract.get_current_tallies()
    assert tallies["opt_0"]["votes"] == 1  # only Alice's legitimate vote
    assert tallies["opt_1"]["votes"] == 0
    assert tallies["_total"] == tallies["opt_0"]["votes"]


def test_vote_with_suspicious_reasoning_may_be_rejected(direct_vm, direct_deploy, direct_alice, monkeypatch):
    """Test that suspicious (coercion-flavored) reasoning gets a low score:
    the vote is recorded but never tallied."""
    question = "Best programming language?"
    options = ["Python", "JavaScript"]
    contract = direct_deploy("voting.py", question, options)

    # Coerced reasoning scores below the threshold, so the vote is recorded
    # but not counted (consistent with the contract's score >= 70 rule).
    direct_vm.mock_llm(
        "Analyze this vote",
        '{"score": 25, "legitimate": false, "notes": "signs of coercion"}',
    )
    direct_vm.sender = direct_alice
    contract.vote_with_reasoning("opt_0", "I was told to vote for Python or else")

    assert contract.has_voted(_to_address(direct_alice)) == True

    record = contract.get_vote_record(_to_address(direct_alice))
    assert "reasoning" in record
    assert "legitimacy_score" in record
    assert "is_legitimate" in record
    assert record["legitimacy_score"] < 70
    assert record["is_legitimate"] is False

    # The low-scoring vote is recorded but never tallied (checked after the
    # deadline, since tallies are hidden while voting is open)
    end_timestamp = int(contract.get_voting_status()["end_timestamp"])
    monkeypatch.setattr(time, "time", lambda: end_timestamp + 100)
    assert contract.get_current_tallies()["opt_0"]["votes"] == 0


def test_vote_invalid_option(direct_vm, direct_deploy, direct_alice):
    """Test voting for invalid option"""
    question = "Test question"
    options = ["Option A", "Option B"]
    contract = direct_deploy("voting.py", question, options)

    direct_vm.sender = direct_alice

    # Try to vote for non-existent option
    with direct_vm.expect_revert("[EXPECTED] Invalid option"):
        contract.vote_with_reasoning("opt_999", "Some reasoning")


def test_end_voting_only_owner(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Test that only owner can end voting, and only after the voting period elapses"""
    question = "Test question"
    options = ["Yes", "No"]
    # Alice deploys the contract, so she is the owner (owner = deploy-time sender)
    direct_vm.sender = direct_alice
    contract = direct_deploy("voting.py", question, options, duration_seconds=1)

    # Bob tries to end voting (not owner)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Only owner"):
        contract.end_voting()

    # Owner tries before the period elapses
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED] Voting period not finished yet"):
        contract.end_voting()


def test_vote_after_end_fails(direct_vm, direct_deploy, direct_alice, monkeypatch):
    """Voting after the period has ended must fail.

    Closes the coverage gap for the ``voting_ended`` guard in
    ``vote_with_reasoning``: end voting for real, then attempt a vote.
    """
    question = "Test question"
    options = ["Yes", "No"]
    # Alice deploys the contract, so she is the owner (owner = deploy-time sender)
    direct_vm.sender = direct_alice
    contract = direct_deploy("voting.py", question, options, duration_seconds=1)

    # The contract reads stdlib time.time() directly (not block height or gl
    # time), so vm.warp cannot move its clock. Advance past the end timestamp
    # deterministically: gltest's direct mode never reads time.time() (only the
    # simulator's StatsCollector does), so patching the shared global is safe
    # here, and pytest's monkeypatch fixture undoes it at test end.
    end_timestamp = int(contract.get_voting_status()["end_timestamp"])
    monkeypatch.setattr(time, "time", lambda: end_timestamp + 100)

    # Owner ends voting once the period has elapsed
    contract.end_voting()
    assert contract.get_voting_status()["ended"] is True

    # A vote attempt after the end reverts on the voting_ended guard
    with direct_vm.expect_revert("[EXPECTED] Voting has ended"):
        contract.vote_with_reasoning("opt_0", "Too late to vote")

    # Results become readable once voting has ended
    results = contract.get_results()
    assert results["_total"] == 0


def test_vote_after_deadline_fails_even_before_end_voting(direct_vm, direct_deploy, direct_alice, monkeypatch):
    """The configured deadline is enforced inside vote_with_reasoning itself.

    Regression for the reviewer flag: votes were accepted after
    end_timestamp until the owner manually ended the poll. Now a vote past
    the deadline reverts even though end_voting() was never called, and the
    poll is automatically considered ended once the deadline passes.
    """
    question = "Test question"
    options = ["Yes", "No"]
    # Alice deploys the contract, so she is the owner (owner = deploy-time sender)
    direct_vm.sender = direct_alice
    contract = direct_deploy("voting.py", question, options, duration_seconds=1)

    # Once the deadline passes, voting is over even though the owner never
    # called end_voting()...
    end_timestamp = int(contract.get_voting_status()["end_timestamp"])
    monkeypatch.setattr(time, "time", lambda: end_timestamp + 100)
    assert contract.get_voting_status()["ended"] is True

    # ...and the deadline check in vote_with_reasoning rejects the vote
    with direct_vm.expect_revert("[EXPECTED] Voting period has ended"):
        contract.vote_with_reasoning("opt_0", "Too late to vote")

    # A vote before the deadline still works (sanity check on the boundary)
    direct_vm.clear_mocks()
    direct_vm.mock_llm(
        "Analyze this vote",
        '{"score": 90, "legitimate": true, "notes": "coherent genuine preference"}',
    )
    monkeypatch.setattr(time, "time", lambda: end_timestamp - 10)
    contract.vote_with_reasoning("opt_0", "Just in time")

    # Tallies are readable again once the deadline has passed
    monkeypatch.setattr(time, "time", lambda: end_timestamp + 100)
    assert contract.get_current_tallies()["opt_0"]["votes"] == 1


def test_vote_at_deadline_boundary(direct_vm, direct_deploy, direct_alice, monkeypatch):
    """Pin the >= deadline semantics: a vote at exactly end_timestamp is
    rejected; one second before it is accepted."""
    question = "Test question"
    options = ["Yes", "No"]
    # Alice deploys the contract, so she is the owner (owner = deploy-time sender)
    direct_vm.sender = direct_alice
    contract = direct_deploy("voting.py", question, options, duration_seconds=1)
    end_timestamp = int(contract.get_voting_status()["end_timestamp"])

    # Exactly at the deadline -> rejected (now >= end_timestamp)
    monkeypatch.setattr(time, "time", lambda: float(end_timestamp))
    with direct_vm.expect_revert("[EXPECTED] Voting period has ended"):
        contract.vote_with_reasoning("opt_0", "Exactly at the deadline")

    # One second before the deadline -> accepted
    direct_vm.mock_llm(
        "Analyze this vote",
        '{"score": 90, "legitimate": true, "notes": "coherent genuine preference"}',
    )
    monkeypatch.setattr(time, "time", lambda: float(end_timestamp) - 1.0)
    contract.vote_with_reasoning("opt_0", "One second early")

    # Past the deadline, results become readable without end_voting()
    monkeypatch.setattr(time, "time", lambda: float(end_timestamp) + 1.0)
    assert contract.get_current_tallies()["opt_0"]["votes"] == 1
    assert contract.get_results()["_total"] == 1


def test_empty_question_rejected(direct_vm, direct_deploy):
    """Test that deployment rejects an empty question"""
    with direct_vm.expect_revert("[EXPECTED] Question cannot be empty"):
        direct_deploy("voting.py", "   ", ["Yes", "No"])


def test_no_options_rejected(direct_vm, direct_deploy):
    """Test that deployment rejects an empty options list"""
    with direct_vm.expect_revert("[EXPECTED] At least one option required"):
        direct_deploy("voting.py", "Test question", [])


def test_zero_duration_rejected(direct_vm, direct_deploy):
    """Test that deployment rejects a zero voting duration"""
    with direct_vm.expect_revert("[EXPECTED] Duration must be > 0"):
        direct_deploy("voting.py", "Test question", ["Yes", "No"], duration_seconds=0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
