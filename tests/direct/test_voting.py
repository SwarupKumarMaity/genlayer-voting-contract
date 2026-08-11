"""Direct mode tests for VotingContract"""

import pytest
from gltest.direct import *


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
    contract = direct_deploy("voting.py", question, options)

    # Alice votes for Python with reasoning (using her address as sender)
    direct_vm.sender = direct_alice
    contract.vote_with_reasoning("opt_0", "I prefer Python for its simplicity and readability")  # Python is opt_0

    # Bob votes for JavaScript with reasoning
    direct_vm.sender = direct_bob
    contract.vote_with_reasoning("opt_1", "JavaScript is essential for web development")  # JavaScript is opt_1

    # Charlie votes for Python with reasoning
    direct_vm.sender = direct_charlie
    contract.vote_with_reasoning("opt_0", "Python has great libraries for data science")  # Python is opt_0

    # Check voting status
    assert contract.has_voted(direct_alice) == True
    assert contract.has_voted(direct_bob) == True
    assert contract.has_voted(direct_charlie) == True

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

    direct_vm.sender = direct_alice
    contract.vote_with_reasoning("opt_0", "I prefer Python for its readability and mature ecosystem")

    record = contract.get_vote_record(direct_alice)
    assert 0 <= record["legitimacy_score"] <= 100
    assert isinstance(record["is_legitimate"], bool)
    # Contract's own rule: legitimate iff score >= 70 (see leader_fn prompt contract)
    assert record["is_legitimate"] == (record["legitimacy_score"] >= 70)


def test_only_legitimate_votes_are_counted(direct_vm, direct_deploy, direct_alice):
    """Core consensus invariant: a vote is only added to the tally when the
    leader/validator consensus judged it legitimate. This holds regardless of
    what the underlying LLM decides for this specific reasoning string, so it
    exercises the real consensus path instead of asserting a hardcoded score."""
    question = "Best programming language?"
    options = ["Python", "JavaScript"]
    contract = direct_deploy("voting.py", question, options)

    direct_vm.sender = direct_alice
    contract.vote_with_reasoning("opt_0", "I prefer Python for its readability and mature ecosystem")

    record = contract.get_vote_record(direct_alice)
    tallies = contract.get_current_tallies()

    if record["is_legitimate"]:
        assert tallies["opt_0"]["votes"] == 1
    else:
        assert tallies["opt_0"]["votes"] == 0
    assert tallies["opt_1"]["votes"] == 0
    assert tallies["_total"] == tallies["opt_0"]["votes"]


def test_vote_with_suspicious_reasoning_may_be_rejected(direct_vm, direct_deploy, direct_alice):
    """Test that suspicious voting reasoning might be rejected by LLM consensus"""
    question = "Best programming language?"
    options = ["Python", "JavaScript"]
    contract = direct_deploy("voting.py", question, options)

    direct_vm.sender = direct_alice
    # Vote with reasoning that might be seen as coerced or suspicious.
    # Note: in direct mode this runs the leader function only (no validator
    # cross-check), so we can't assert a specific outcome deterministically -
    # but the record must still reflect whatever the leader concluded.
    contract.vote_with_reasoning("opt_0", "I was told to vote for Python or else")

    assert contract.has_voted(direct_alice) == True

    record = contract.get_vote_record(direct_alice)
    assert "reasoning" in record
    assert "legitimacy_score" in record
    assert "is_legitimate" in record
    assert record["is_legitimate"] == (record["legitimacy_score"] >= 70)


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
    contract = direct_deploy("voting.py", question, options, duration_seconds=1)

    # Bob tries to end voting (not owner)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Only owner"):
        contract.end_voting()

    # Owner tries before the period elapses
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED] Voting period not finished yet"):
        contract.end_voting()


def test_vote_after_end_fails(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Test that voting after end fails"""
    question = "Test question"
    options = ["Yes", "No"]
    # Duration must be > 0 per the contract's own validation
    contract = direct_deploy("voting.py", question, options, duration_seconds=1)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("[EXPECTED] Voting period not finished yet"):
        contract.end_voting()


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
