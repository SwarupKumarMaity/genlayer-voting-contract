"""Regression tests for the score/flag consistency invariant.

The contract's core rule is: a vote counts iff its normalized legitimacy
score is >= 70. An earlier version read the model's `legitimate` field
directly instead of deriving it, so a model reply like
``{"score": 85, "legitimate": false}`` rejected a vote that scored 85, and
``{"score": 20, "legitimate": true}`` counted one that scored 20.

These tests mock the LLM with exactly those adversarial replies, so they
fail against the old logic and pass against the derived-flag logic.
"""

import time

import pytest
from gltest.direct import *

PROMPT_PATTERN = "Analyze this vote"
THRESHOLD = 70


def _deploy_and_vote(direct_vm, direct_deploy, direct_alice, llm_response, monkeypatch):
    """Deploy, register one LLM reply, cast a single vote as Alice, then move
    the clock past the deadline so tallies/results are readable."""
    contract = direct_deploy("voting.py", "Best programming language?", ["Python", "JavaScript"])
    direct_vm.mock_llm(PROMPT_PATTERN, llm_response)
    direct_vm.sender = direct_alice
    contract.vote_with_reasoning("opt_0", "I prefer Python for its readability")

    # Voting period elapsed -> get_current_tallies()/get_results() work
    end_timestamp = int(contract.get_voting_status()["end_timestamp"])
    monkeypatch.setattr(time, "time", lambda: end_timestamp + 100)
    return contract


def _sole_record(contract):
    """The one vote record cast in these single-voter tests.

    Looked up via get_all_vote_records rather than get_vote_record(addr):
    records are keyed by str(Address), while the fixture hands out raw
    bytes, so a direct lookup would miss for reasons unrelated to these
    assertions.
    """
    records = contract.get_all_vote_records()
    assert len(records) == 1, f"expected exactly one vote record, got {len(records)}"
    return next(iter(records.values()))


def test_high_score_counts_even_when_model_says_illegitimate(
    direct_vm, direct_deploy, direct_alice, monkeypatch
):
    """score 85 + legitimate:false -> the score wins, the vote counts."""
    contract = _deploy_and_vote(
        direct_vm, direct_deploy, direct_alice,
        '{"score": 85, "legitimate": false, "notes": "coherent genuine preference"}',
        monkeypatch,
    )

    record = _sole_record(contract)
    assert record["legitimacy_score"] == 85
    assert record["is_legitimate"] is True
    assert contract.get_current_tallies()["opt_0"]["votes"] == 1


def test_low_score_rejected_even_when_model_says_legitimate(
    direct_vm, direct_deploy, direct_alice, monkeypatch
):
    """score 20 + legitimate:true -> the score wins, the vote is not counted."""
    contract = _deploy_and_vote(
        direct_vm, direct_deploy, direct_alice,
        '{"score": 20, "legitimate": true, "notes": "signs of coercion"}',
        monkeypatch,
    )

    record = _sole_record(contract)
    assert record["legitimacy_score"] == 20
    assert record["is_legitimate"] is False
    assert contract.get_current_tallies()["opt_0"]["votes"] == 0


@pytest.mark.parametrize(
    "raw_score,expected_score,expected_legit",
    [
        (69, 69, False),   # just below threshold
        (70, 70, True),    # exactly at threshold
        (-5, 0, False),    # clamped up
        (150, 100, True),  # clamped down
    ],
)
def test_threshold_and_clamping(
    direct_vm, direct_deploy, direct_alice, raw_score, expected_score, expected_legit, monkeypatch
):
    contract = _deploy_and_vote(
        direct_vm, direct_deploy, direct_alice,
        '{"score": %s, "legitimate": true, "notes": "assessment"}' % raw_score,
        monkeypatch,
    )

    record = _sole_record(contract)
    assert record["legitimacy_score"] == expected_score
    assert record["is_legitimate"] is expected_legit
    assert record["is_legitimate"] == (record["legitimacy_score"] >= THRESHOLD)


def test_unparseable_score_falls_back_to_50_and_is_rejected(
    direct_vm, direct_deploy, direct_alice, monkeypatch
):
    """A garbage score must not become a legitimate vote."""
    contract = _deploy_and_vote(
        direct_vm, direct_deploy, direct_alice,
        '{"score": "not-a-number", "legitimate": true, "notes": "assessment"}',
        monkeypatch,
    )

    record = _sole_record(contract)
    assert record["legitimacy_score"] == 50
    assert record["is_legitimate"] is False
    assert contract.get_current_tallies()["opt_0"]["votes"] == 0


NOTES = "coherent genuine preference"


def test_validator_accepts_consistent_leader(
    direct_vm, direct_deploy, direct_alice, monkeypatch
):
    """Sanity check: a leader whose flag matches its score passes consensus."""
    _deploy_and_vote(
        direct_vm, direct_deploy, direct_alice,
        '{"score": 85, "legitimate": true, "notes": "%s"}' % NOTES,
        monkeypatch,
    )

    assert direct_vm.run_validator(
        leader_result={"score": 85, "legitimate": True, "info": NOTES}
    ) is True


def test_validator_rejects_leader_whose_flag_contradicts_its_score(
    direct_vm, direct_deploy, direct_alice, monkeypatch
):
    """The case agreement alone cannot catch.

    The model returns a self-contradictory pair (85 / false), so a leader
    reporting that pair and a validator re-running the same prompt would
    *agree with each other* while both violate the >= 70 rule. Only a check
    of the pair itself rejects this.
    """
    _deploy_and_vote(
        direct_vm, direct_deploy, direct_alice,
        '{"score": 85, "legitimate": false, "notes": "%s"}' % NOTES,
        monkeypatch,
    )

    assert direct_vm.run_validator(
        leader_result={"score": 85, "legitimate": False, "info": NOTES}
    ) is False


def test_validator_rejects_low_score_claimed_legitimate(
    direct_vm, direct_deploy, direct_alice, monkeypatch
):
    """Mirror case: 20 / true, again agreed on by leader and validator."""
    _deploy_and_vote(
        direct_vm, direct_deploy, direct_alice,
        '{"score": 20, "legitimate": true, "notes": "%s"}' % NOTES,
        monkeypatch,
    )

    assert direct_vm.run_validator(
        leader_result={"score": 20, "legitimate": True, "info": NOTES}
    ) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
