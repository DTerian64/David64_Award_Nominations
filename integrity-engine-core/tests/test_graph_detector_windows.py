from datetime import datetime, timedelta, timezone
import pytest
from integrity_engine.graph.history_windows import detector_windows, filter_detector_history
from integrity_engine.graph.candidate_edge_evaluation import GraphInferenceSnapshot, CandidateNomination, SnapshotNomination, evaluate_candidate_edge_for_ring


def test_alias_fallback_and_invalid_windows():
    windows = detector_windows({"detection_window_days": 180, "detector_windows": {"Ring": 60, "CopyPasteFraud": 270}})
    assert windows["Ring"] == 60 and windows["CopyPaste"] == 270
    assert windows["SuperNominator"] == 180
    for overrides in ({"Ring": 0}, {"Ring": True}, {"Ring": 60.5}, {"Typo": 60}, {"CopyPaste": 60, "CopyPasteFraud": 90}):
        with pytest.raises(ValueError):
            detector_windows({"detector_windows": overrides})


def test_live_ring_excludes_older_edges_but_other_detectors_keep_them():
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    history = tuple(SnapshotNomination(i + 1, a, b, 500, "Paid", now - timedelta(days=age))
                    for i, (a, b, age) in enumerate([(2, 3, 90), (3, 1, 90), (5, 6, 60)]))
    policy = {"detection_window_days": 180, "detector_windows": {"Ring": 60}, "thresholds": {"low": 25, "medium": 50, "high": 75, "critical": 100},
              "patterns": {"Ring": {"enabled": True, "parameters": {}, "base_score": 35}}}
    snapshot = GraphInferenceSnapshot(5, "run", 1, now, 180, policy, history)
    candidate = CandidateNomination(999, 1, 2, 500, now)
    assert evaluate_candidate_edge_for_ring(snapshot, candidate) is None
    assert len(snapshot.history_for_candidate(candidate, "Ring")) == 1
    assert len(snapshot.history_for_candidate(candidate, "SuperNominator")) == 3
    rows = [{"CreatedAt": item.created_at, "NominationId": item.nomination_id} for item in history]
    assert [r["NominationId"] for r in filter_detector_history(rows, 60, now)] == [3]


def test_legacy_snapshot_uses_original_window():
    assert detector_windows({}, 365)["Ring"] == 365
