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
    assert contract.get_voting_status()["ended"] == False
    assert contract.get_voting_status()["current_block"] >= 0

    # Check options were created
    results = contract.get_results()
    # Should fail since voting not ended yet
    with direct_vm.expect_revert("[EXPECTED] Voting not ended"):
        contract.get_results()


def test_vote_with_reasoning_and_get_results(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    """Test voting with reasoning and getting results"""
    question = "Best programming language?"
    options = ["Python", "JavaScript", "Go"]
    contract = direct_deploy("voting.py", question, options)

    # Alice votes for Python with reasoning
    direct_vm.sender = direct_alice
    contract.vote_with_reasoning(direct_alice, "opt_0", "I prefer Python for its simplicity and readability")  # Python is opt_0

    # Bob votes for JavaScript with reasoning
    direct_vm.sender = direct_bob
    contract.vote_with_reasoning(direct_bob, "opt_1", "JavaScript is essential for web development")  # JavaScript is opt_1

    # Charlie votes for Python with reasoning
    direct_vm.sender = direct_charlie
    contract.vote_with_reasoning(direct_charlie, "opt_0", "Python has great libraries for data science")  # Python is opt_0

    # Check voting status
    assert contract.has_voted(direct_alice) == True
    assert contract.has_voted(direct_bob) == True
    assert contract.has_voted(direct_charlie) == True

    # Try to vote again - should fail
    with direct_vm.expect_revert("[EXPECTED] Already voted"):
        contract.vote_with_reasoning(direct_alice, "opt_2")

    # End voting (owner only)
    direct_vm.sender = direct_alice  # Assuming alice is owner
    contract.end_voting()

    # Check voting ended
    assert contract.get_voting_status()["ended"] == True

    # Get results
    results = contract.get_results()
    assert results["opt_0"]["votes"] == 2  # Python: Alice + Charlie
    assert results["opt_1"]["votes"] == 1  # JavaScript: Bob
    assert results["opt_2"]["votes"] == 0  # Go: nobody
    assert results["_total"] == 3


def test_vote_with_suspicious_reasoning_may_be_rejected(direct_vm, direct_deploy, direct_alice):
    """Test that suspicious voting reasoning might be rejected by LLM consensus"""
    question = "Best programming language?"
    options = ["Python", "JavaScript"]
    contract = direct_deploy("voting.py", question, options)

    direct_vm.sender = direct_alice
    # Vote with reasoning that might be seen as coerced or suspicious
    # Note: In direct mode, this will use leader function only (no consensus)
    # In full consensus, validators might reject this based on LLM analysis
    contract.vote_with_reasoning(direct_alice, "opt_0", "I was told to vote for Python")

    # Check that vote was recorded (in direct mode)
    assert contract.has_voted(direct_alice) == True

    # Get vote record to see legitimacy score
    record = contract.get_vote_record(direct_alice)
    assert "reasoning" in record
    assert "legitimacy_score" in record
    assert "is_legitimate" in record


def test_vote_invalid_option(direct_vm, direct_deploy, direct_alice):
    """Test voting for invalid option"""
    question = "Test question"
    options = ["Option A", "Option B"]
    contract = direct_deploy("voting.py", question, options)

    direct_vm.sender = direct_alice

    # Try to vote for non-existent option
    with direct_vm.expect_revert("[EXPECTED] Invalid option"):
        contract.vote_with_reasoning(direct_alice, "opt_999", "Some reasoning")


def test_end_voting_only_owner(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Test that only owner can end voting"""
    question = "Test question"
    options = ["Yes", "No"]
    contract = direct_deploy("voting.py", question, options)

    # Bob tries to end voting (not owner)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Only owner"):
        contract.end_voting()

    # Owner can end voting
    direct_vm.sender = direct_alice
    contract.end_voting()

    assert contract.get_voting_status()["ended"] == True


def test_vote_after_end_fails(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Test that voting after end fails"""
    question = "Test question"
    options = ["Yes", "No"]
    contract = direct_deploy("voting.py", question, options, duration_blocks=0)  # End immediately

    # End voting immediately
    direct_vm.sender = direct_alice
    contract.end_voting()

    # Try to vote after end - should fail
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("[EXPECTED] Voting has ended"):
        contract.vote_with_reasoning(direct_bob, "opt_0", "Some reasoning")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])