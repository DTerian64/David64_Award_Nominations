"""Continuous candidate scores stay separate from routing eligibility."""

from datetime import datetime, timedelta, timezone

from integrity_engine import (
    CandidateNomination,
    GraphInferenceSnapshot,
    SnapshotNomination,
    evaluate_candidate_detectors,
    evaluate_no_finding,
    evaluate_super_beneficiary,
)


NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


def _config(base, routing=True, **parameters):
    return {
        "enabled": True,
        "enabled_for_routing": routing,
        "base_score": base,
        "minimum_score": 0,
        "maximum_score": 100,
        "parameters": parameters,
    }


POLICY = {
    "thresholds": {"low": 25, "medium": 50, "high": 75, "critical": 90},
    "patterns": {
        "Ring": _config(35),
        "SuperNominator": _config(
            35, minimum_count=5, standard_deviations=2,
            median_multiplier=3, excess_weight=30, volume_weight=20,
            exposure_weight=15, amount_reference=10_000,
        ),
        "SuperBeneficiary": _config(
            20, minimum_count=5, minimum_unique_nominators=4,
            standard_deviations=2, median_multiplier=3, unique_reference=10,
            compactness_reference_days=14, amount_reference=10_000,
            excess_weight=20, breadth_weight=20,
            repeat_concentration_weight=10, compactness_weight=15,
            exposure_weight=15,
        ),
        "TemporalBurst": _config(
            25, burst_window_days=3, minimum_baseline_days=21,
            minimum_nominations=8, standard_deviations=3,
            count_reference=20, amount_reference=10_000,
            excess_weight=25, volume_weight=15,
            participant_concentration_weight=15,
            temporal_compactness_weight=10, exposure_weight=10,
        ),
        "BipartiteDenseBlock": _config(
            30, minimum_side_size=2, minimum_large_side_size=3,
            minimum_shared_neighbors=2, overlap_threshold=.6,
            minimum_density=.65, minimum_edges=6, repeat_reference=2,
            compactness_reference_days=14, amount_reference=10_000,
            density_weight=20, overlap_weight=15, exclusivity_weight=10,
            repeat_weight=10, compactness_weight=5, exposure_weight=10,
        ),
        "CopyPaste": _config(
            35, similarity_threshold=.92, minimum_cluster_size=3,
            cluster_size_reference=8, amount_reference=10_000,
            similarity_weight=35, cluster_size_weight=20,
            exposure_weight=10,
        ),
    },
}


def _nom(identifier, source, target, days=1, description="A sufficiently long nomination description"):
    return SnapshotNomination(
        identifier, source, target, 1_000, "Approved",
        NOW - timedelta(days=days), description,
    )


def _snapshot(*nominations):
    return GraphInferenceSnapshot(
        tenant_id=1,
        run_id="run-1",
        policy_version=1,
        generated_at=NOW,
        window_days=365,
        scoring_policy=POLICY,
        nominations=tuple(nominations),
    )


def _candidate():
    return CandidateNomination(
        99, 10, 20, 1_000, NOW,
        "A sufficiently long candidate nomination description",
    )


def test_ineligible_detector_keeps_its_formula_base_score():
    result = evaluate_no_finding(_snapshot(), "Ring", "No completed path")
    assert result.score == 35
    assert result.state == "NOT_SCORING"
    assert not result.eligible


def test_repeated_pair_is_not_automatically_a_super_beneficiary():
    history = [_nom(index, 10, 20, days=index) for index in range(1, 9)]
    history.extend([_nom(20, 30, 40), _nom(21, 31, 41), _nom(22, 32, 42)])
    result = evaluate_super_beneficiary(_snapshot(*history), _candidate())
    assert result.score >= POLICY["patterns"]["SuperBeneficiary"]["base_score"]
    assert result.state == "NOT_SCORING"
    assert any("unique nominators" in reason for reason in result.eligibility_reasons)


def test_copy_paste_component_is_scored_by_shared_policy_formula():
    snapshot = _snapshot(_nom(1, 11, 21), _nom(2, 12, 22))
    results = evaluate_candidate_detectors(
        snapshot,
        _candidate(),
        copy_paste_component_nomination_ids={1, 2},
        copy_paste_qualifying_similarities=[.96, .95],
    )
    copy_paste = next(item for item in results if item.detector == "CopyPaste")
    assert copy_paste.eligible
    assert copy_paste.state == "SCORING"
    assert copy_paste.score > POLICY["patterns"]["CopyPaste"]["base_score"]
    assert copy_paste.supporting_nomination_ids == (1, 2, 99)
