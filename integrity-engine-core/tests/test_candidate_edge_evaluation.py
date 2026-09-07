"""Tests for deterministic candidate-edge evaluation by Graph detectors."""

from datetime import datetime, timedelta, timezone
import random

import pytest

from integrity_engine import (
    CandidateNomination,
    EvaluationLimitExceeded,
    GraphInferenceSnapshot,
    SnapshotNomination,
    evaluate_candidate_edge_for_ring,
)
from integrity_engine.graph.finding_scoring import calculate_graph_finding_score


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
            "candidate_evaluation": {
                "max_states": 100_000,
                "max_ring_size": 8,
                "limit_strategy": "BEST_EVIDENCE",
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
    result = evaluate_candidate_edge_for_ring(
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
    assert evaluate_candidate_edge_for_ring(graph, candidate()) is None


def test_two_person_reciprocity_is_not_a_ring():
    assert evaluate_candidate_edge_for_ring(
        snapshot(nomination(10, 2, 1)), candidate()
    ) is None


def test_current_and_future_nominations_are_excluded():
    graph = snapshot(
        nomination(99, 2, 3),
        nomination(10, 3, 1, when=NOW + timedelta(seconds=1)),
    )
    assert evaluate_candidate_edge_for_ring(graph, candidate()) is None


def test_highest_score_path_wins_deterministically():
    graph = snapshot(
        nomination(10, 2, 3, 100), nomination(11, 3, 1, 100),
        nomination(20, 2, 4, 9000), nomination(21, 4, 1, 9000),
    )
    result = evaluate_candidate_edge_for_ring(graph, candidate())
    assert result is not None
    assert result.path_user_ids == (2, 4, 1, 2)
    assert result.paths_considered == 2


def test_work_limit_returns_best_concrete_evidence_as_a_lower_bound():
    graph = snapshot(
        nomination(10, 2, 3, 100), nomination(11, 3, 1, 100),
        nomination(20, 2, 4, 9000), nomination(21, 4, 5, 9000),
        nomination(22, 5, 1, 9000),
    )
    result = evaluate_candidate_edge_for_ring(graph, candidate(), max_states=1)
    assert result is not None
    assert result.path_user_ids == (2, 3, 1, 2)
    assert result.search_status == "BOUNDED"
    assert result.search_complete is False
    assert result.score_semantics == "LOWER_BOUND"
    assert result.configured_max_states == 1
    assert result.remaining_score_upper_bound is not None


def test_elce_can_require_a_complete_search():
    graph = snapshot(
        nomination(10, 2, 3, 100), nomination(11, 3, 1, 100),
        nomination(20, 2, 4, 9000), nomination(21, 4, 5, 9000),
        nomination(22, 5, 1, 9000),
    )
    with pytest.raises(EvaluationLimitExceeded):
        evaluate_candidate_edge_for_ring(
            graph, candidate(), max_states=1, require_complete=True
        )


def test_candidate_evaluation_settings_are_read_from_the_ring_policy():
    policy = {
        **POLICY,
        "patterns": {
            "Ring": {
                **POLICY["patterns"]["Ring"],
                "candidate_evaluation": {
                    "max_states": 1,
                    "max_ring_size": 8,
                    "limit_strategy": "BEST_EVIDENCE",
                },
            }
        },
    }
    graph = GraphInferenceSnapshot(
        tenant_id=1, run_id="run-policy", policy_version=4,
        generated_at=NOW, window_days=365, scoring_policy=policy,
        nominations=(
            nomination(10, 2, 3, 100), nomination(11, 3, 1, 100),
            nomination(20, 2, 4, 9000), nomination(21, 4, 5, 9000),
            nomination(22, 5, 1, 9000),
        ),
    )
    result = evaluate_candidate_edge_for_ring(graph, candidate())
    assert result is not None
    assert result.configured_max_states == 1
    assert result.search_status == "BOUNDED"


def test_result_is_deterministic_when_snapshot_input_order_changes():
    items = (
        nomination(10, 2, 3, 100), nomination(11, 3, 1, 100),
        nomination(20, 2, 4, 9000), nomination(21, 4, 1, 9000),
        nomination(30, 2, 5, 4000), nomination(31, 5, 1, 4000),
    )
    forward = evaluate_candidate_edge_for_ring(snapshot(*items), candidate())
    reverse = evaluate_candidate_edge_for_ring(snapshot(*reversed(items)), candidate())
    assert forward is not None and reverse is not None
    assert forward.to_dict() == reverse.to_dict()


def test_pruned_search_matches_exhaustive_search_on_small_graphs():
    generator = random.Random(20260907)
    ring = POLICY["patterns"]["Ring"]

    for case in range(40):
        items = []
        edge_amount = {}
        identifier = 1000 + case * 100
        for source in range(1, 7):
            for target in range(1, 7):
                if source == target or generator.random() >= 0.34:
                    continue
                amount = generator.randint(1, 20) * 100
                items.append(nomination(identifier, source, target, amount))
                edge_amount[(source, target)] = amount
                identifier += 1

        best_score = None

        def visit(path):
            nonlocal best_score
            node = path[-1]
            used_edges = len(path) - 1
            if node == 1 and used_edges >= 2:
                total = 2000 + sum(
                    edge_amount[(source, destination)]
                    for source, destination in zip(path, path[1:])
                )
                signals = {
                    "exposure": min(total / 10_000, 1.0),
                    "repeat": min((used_edges + 1) / (len(path) * 3), 1.0),
                    "compactness": max(0.0, 1.0 - ((len(path) - 3) / 5.0)),
                }
                score, _components = calculate_graph_finding_score(
                    base_score=ring["base_score"],
                    minimum_score=ring["minimum_score"],
                    maximum_score=ring["maximum_score"],
                    parameters=ring["parameters"],
                    signals=signals,
                )
                best_score = score if best_score is None else max(best_score, score)
                return
            if used_edges >= 7:
                return
            for neighbor in range(1, 7):
                if (node, neighbor) in edge_amount and neighbor not in path:
                    visit(path + (neighbor,))

        visit((2,))
        result = evaluate_candidate_edge_for_ring(snapshot(*items), candidate())
        assert (result is None) == (best_score is None), f"case {case}"
        if result is not None:
            assert result.search_complete is True
            assert result.score == best_score, f"case {case}"


def test_snapshot_contract_round_trips_and_rejects_duplicate_ids():
    original = snapshot(nomination(10, 2, 3))
    restored = GraphInferenceSnapshot.from_dict(original.to_dict())
    assert restored == original

    invalid = original.to_dict()
    invalid["nominations"].append(dict(invalid["nominations"][0]))
    with pytest.raises(ValueError, match="duplicate nomination IDs"):
        GraphInferenceSnapshot.from_dict(invalid)
