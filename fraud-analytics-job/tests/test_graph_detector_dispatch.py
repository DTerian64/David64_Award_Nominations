"""Each batch detector sees its own interval, and publication records it."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from modeling import graph_analytics as graph


def test_batch_dispatch_and_snapshot_keep_detector_specific_windows(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [{"NominationId": i, "CreatedAt": now - timedelta(days=age)}
            for i, age in enumerate((10, 90, 200), 1)]
    names = {"Ring": "detect_rings", "BipartiteDenseBlock": "detect_bipartite_dense_blocks",
             "TemporalBurst": "detect_temporal_bursts", "SuperNominator": "detect_super_nominators",
             "SuperBeneficiary": "detect_super_beneficiaries", "CopyPaste": "detect_copy_paste",
             "HiddenCandidate": "detect_hidden_candidate", "LowRecognitionNominator": "detect_low_recognition_nominators"}
    policy = {"version": 3, "strategy": "MAX_RELEVANT_FINDING", "snapshot_max_age_days": 14,
              "detection_window_days": 180,
              "detector_windows": {"Ring": 60, "CopyPasteFraud": 30, "HiddenCandidate": 270},
              "patterns": {name: {"enabled": True} for name in [*names, "Desert"]}}
    monkeypatch.setattr(graph, "_load_active_graph_policy", lambda *args: policy)
    load = MagicMock(return_value=rows)
    monkeypatch.setattr(graph, "_load_nominations", load)
    monkeypatch.setattr(graph, "_load_users", lambda *args: [])
    monkeypatch.setattr(graph, "_load_ever_active_user_ids", lambda *args: {999})
    mocks = {}
    for name, function in names.items():
        mocks[name] = MagicMock(return_value=[])
        monkeypatch.setattr(graph, function, mocks[name])
    desert = MagicMock(return_value=[])
    monkeypatch.setattr(graph, "detect_deserts", desert)
    publish = MagicMock(return_value={"blob_name": "blob", "sha256": "sha", "schema_version": 2,
                                      "size_bytes": 100, "generated_at": now.isoformat()})
    monkeypatch.setattr(graph, "_publish_graph_inference_snapshot", publish)
    monkeypatch.setattr(graph, "_save_findings", MagicMock())
    monkeypatch.setattr(graph, "_populate_graph_flag_snapshots", MagicMock())
    status = MagicMock()
    monkeypatch.setattr(graph, "upsert_component_status", status)
    connection = MagicMock()
    assert graph._process_tenant(connection, 5, "dbo.GraphPatternFindings", 180, "run") == 0
    assert load.call_args.args[2] == 270
    for name in names:
        expected = [1] if name in ("Ring", "CopyPaste") else [1, 2, 3] if name == "HiddenCandidate" else [1, 2]
        assert [row["NominationId"] for row in mocks[name].call_args.args[0]] == expected
    assert desert.call_args.args[0] == {999}
    assert publish.call_args.kwargs["window_days"] == 270
    assert publish.call_args.kwargs["policy"]["detector_windows"]["Ring"] == 60
    diagnostics = status.call_args.kwargs["diagnostics"]
    assert diagnostics["detector_nomination_counts"]["Ring"] == 1
    assert diagnostics["detector_windows"]["CopyPaste"] == 30
