"""Tests for the GNN's strictly-prior causal topology contract."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from integrity_engine.gnn import (
    CAUSAL_CONTEXT_FEATURE_COLUMNS,
    causal_context_values,
)


NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


def _row(identifier, source, target, when, *, eligible=True):
    return {
        "NominationId": identifier,
        "NominatorId": source,
        "BeneficiaryId": target,
        "CreatedAt": when,
        "IsBehaviorEligible": eligible,
    }


def test_ring_target_sees_reverse_two_hop_path_but_not_itself_or_future():
    target = _row(30, 3, 1, NOW)
    rows = [
        _row(10, 1, 2, NOW - timedelta(minutes=2)),
        _row(20, 2, 3, NOW - timedelta(minutes=1)),
        target,
        _row(40, 1, 3, NOW + timedelta(minutes=1)),
    ]

    values = causal_context_values(rows, target, window_days=365)

    assert values["LogReverseTwoHopPathCount"] > 0
    assert values["LogReverseThreeHopPathCount"] == 0
    assert values["LogPriorDirectedPairCount"] == 0
    assert values["LogPriorReversePairCount"] == 0


def test_four_user_ring_target_sees_reverse_three_hop_path():
    target = _row(40, 4, 1, NOW)
    rows = [
        _row(10, 1, 2, NOW - timedelta(minutes=3)),
        _row(20, 2, 3, NOW - timedelta(minutes=2)),
        _row(30, 3, 4, NOW - timedelta(minutes=1)),
        target,
    ]

    values = causal_context_values(rows, target, window_days=365)

    assert values["LogReverseThreeHopPathCount"] > 0
    assert values["LogReverseTwoHopPathCount"] == 0


def test_same_timestamp_uses_nomination_id_as_causal_tie_breaker():
    target = _row(30, 2, 1, NOW)
    rows = [
        _row(20, 1, 2, NOW),
        target,
        _row(40, 1, 2, NOW),
    ]

    values = causal_context_values(rows, target, window_days=365)

    assert values["LogPriorReversePairCount"] > 0
    assert values["LogPriorDirectedPairCount"] == 0


def test_ineligible_and_out_of_window_edges_are_excluded():
    target = _row(30, 2, 1, NOW)
    rows = [
        _row(10, 1, 2, NOW - timedelta(days=366)),
        _row(20, 1, 2, NOW - timedelta(minutes=1), eligible=False),
    ]

    values = causal_context_values(rows, target, window_days=365)

    assert list(values) == list(CAUSAL_CONTEXT_FEATURE_COLUMNS)
    assert all(value == 0 for value in values.values())


def test_active_burst_is_visible_in_one_hour_features():
    target = _row(30, 3, 9, NOW)
    rows = [
        _row(10, 1, 9, NOW - timedelta(minutes=2)),
        _row(20, 2, 9, NOW - timedelta(minutes=1)),
    ]

    values = causal_context_values(rows, target, window_days=365)

    assert values["LogBeneficiaryIncomingCount1h"] > 0
    assert values["LogEndpointEdgeCount1h"] > 0
