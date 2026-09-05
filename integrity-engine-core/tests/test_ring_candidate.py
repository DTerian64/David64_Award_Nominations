from datetime import datetime, timedelta, timezone

import pytest

from integrity_engine import (
    CandidateNomination,
    EvaluationLimitExceeded,
    GraphInferenceSnapshot,
    SnapshotNomination,
    evaluate_ring_candidate,
)


NOW = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
POLICY = {
    "thresholds": {"low": 20, "medium": 40, "high": 60, "critical": 80},
    "patterns": {
        "Ring": {
            "enabled": True,
            "enabled_for_routing": True,
            "applicable_roles": ["nominator", "beneficiary"],
            "base_score": 35,
            "minimum_score": 0,
            "maximum_score": 100,
            "parameters": {
                "amount_reference": 10_000,
                "exposure_weight": 35,
                "repeat_weight": 15,
                "compactness_weight": 15,
            },
        }
    },
}


def nomination(identifier, source, target, amount=1000, *, when=None):
    return SnapshotNomination(
        identifier, source, target, amount, "Approved", when or NOW - timedelta(days=1)
    )


def snapshot(*items):
    return GraphInferenceSnapshot(
        tenant_id=1,
        run_id="run-1",
        policy_version=3,
        generated_at=NOW,
        window_days=365,
        scoring_policy=POLICY,
        nominations=tuple(items),
    )


def candidate(identifier=99, source=1, target=2, amount=2000):
    return CandidateNomination(identifier, source, target, amount, NOW)


def test_candidate_edge_closes_directed_ring_with_lineage():
    result = evaluate_ring_candidate(
        snapshot(nomination(10, 2, 3), nomination(11, 3, 1)), candidate()
    )
    assert result is not None
    assert result.path_user_ids == (2, 3, 1, 2)
    assert result.supporting_nomination_ids == (10, 11, 99)
    assert result.evidence_scope == "CURRENT_NOMINATION"


def test_unrelated_nominator_and_beneficiary_rings_do_not_score():
    graph = snapshot(
        nomination(10, 1, 3), nomination(11, 3, 4), nomination(12, 4, 1),
        nomination(20, 2, 5), nomination(21, 5, 6), nomination(22, 6, 2),
    )
    assert evaluate_ring_candidate(graph, candidate()) is None


def test_two_person_reciprocity_is_not_a_ring():
    assert evaluate_ring_candidate(snapshot(nomination(10, 2, 1)), candidate()) is None


def test_current_and_future_nominations_are_excluded():
    graph = snapshot(
        nomination(99, 2, 3),
        nomination(10, 3, 1, when=NOW + timedelta(seconds=1)),
    )
    assert evaluate_ring_candidate(graph, candidate()) is None


def test_highest_score_path_wins_deterministically():
    graph = snapshot(
        nomination(10, 2, 3, 100), nomination(11, 3, 1, 100),
        nomination(20, 2, 4, 9000), nomination(21, 4, 1, 9000),
    )
    result = evaluate_ring_candidate(graph, candidate())
    assert result is not None
    assert result.path_user_ids == (2, 4, 1, 2)
    assert result.paths_considered == 2


def test_work_limit_fails_loudly():
    graph = snapshot(
        nomination(10, 2, 3), nomination(11, 3, 4), nomination(12, 4, 1)
    )
    with pytest.raises(EvaluationLimitExceeded):
        evaluate_ring_candidate(graph, candidate(), max_states=1)


def test_snapshot_contract_round_trips_and_rejects_duplicate_ids():
    original = snapshot(nomination(10, 2, 3))
    restored = GraphInferenceSnapshot.from_dict(original.to_dict())
    assert restored == original

    invalid = original.to_dict()
    invalid["nominations"].append(dict(invalid["nominations"][0]))
    with pytest.raises(ValueError, match="duplicate nomination IDs"):
        GraphInferenceSnapshot.from_dict(invalid)
