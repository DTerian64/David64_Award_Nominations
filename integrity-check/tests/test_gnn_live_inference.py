"""Published live encoding contract, manifest checks and no stale fallback."""
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch, MagicMock
import pytest
import torch

os.environ.setdefault("AZURE_STORAGE_ACCOUNT", "teststorage")
os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integrity-engine-core" / "src"))

from inference import gnn_check, gnn_live, decision_contract
from integrity_engine.gnn.encoder import HeteroEncoder
from integrity_engine.gnn.features import USER_FEATURE_COLUMNS, NOMINATION_FEATURE_COLUMNS
from integrity_engine.gnn.live_graph import LIVE_ENCODING_CONTRACT, build_live_graph_inputs


def bundle_fixture():
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    users = [{"UserId": 1, "TenantId": 5}, {"UserId": 2, "TenantId": 5}]
    target = {"NominationId": 100, "NominatorId": 1, "BeneficiaryId": 2, "CreatedAt": now}
    head = {"model_version": "gnn-v4-test", "architecture": "graphsage", "graph_snapshot_id": "graph-test",
            "feature_schema_version": "gnn-v2-causal-v1", "model_schema_version": 4, "emb_dim": 8,
            "tenant_id": 5, "inference_contract": LIVE_ENCODING_CONTRACT,
            "user_feature_columns": USER_FEATURE_COLUMNS, "nomination_feature_columns": NOMINATION_FEATURE_COLUMNS,
            "user_scaler_mean": [0.] * 3, "user_scaler_std": [1.] * 3,
            "nomination_scaler_mean": [0.] * len(NOMINATION_FEATURE_COLUMNS),
            "nomination_scaler_std": [1.] * len(NOMINATION_FEATURE_COLUMNS),
            "category_amount_stats": {"global": {"median": 100., "scale": 100.}},
            "causal_context_window_days": 365, "training_policy_id": 5, "training_policy_version": 1,
            "graph_snapshot_as_of": "2026-09-21", "calibration": {"OVERALL": {"slope": 1., "intercept": 0.}},
            "pattern_head_states": {}, "amount_mean": 100., "amount_std": 100.}
    graph = build_live_graph_inputs(users, [], target, head)
    encoder = HeteroEncoder(hidden_dim=8, out_dim=8).eval()
    encoder(graph.x_dict, graph.edge_index_dict)
    artifact = {key: head[key] for key in ("tenant_id", "inference_contract", "model_version", "architecture",
                                         "graph_snapshot_id", "feature_schema_version", "emb_dim")}
    artifact.update(encoder_state_dict=encoder.state_dict(), hidden_dim=8, num_layers=2)
    stream = io.BytesIO()
    torch.save(artifact, stream)
    raw = stream.getvalue()
    head["_artifact_sha256"], head["_artifact_size_bytes"] = hashlib.sha256(b"decoder").hexdigest(), 7
    manifest = {key: head[key] for key in ("tenant_id", "inference_contract", "model_version", "graph_snapshot_id")}
    manifest["artifacts"] = [
        {"role": "serving_encoder", "relative_path": "serving/encoder.pt", "size_bytes": len(raw),
         "sha256": hashlib.sha256(raw).hexdigest()},
        {"role": "serving_decoder", "relative_path": "serving/decoder.pt", "size_bytes": 7,
         "sha256": head["_artifact_sha256"]},
    ]
    blobs = {
        "tenant_5/awards/gnn/gnn-v4-test/manifest.json": (
            json.dumps(manifest).encode()
        ),
        "tenant_5/awards/gnn/gnn-v4-test/serving/encoder.pt": raw,
    }
    return head, users, target, blobs


def test_encoder_is_manifest_verified_and_cached_with_frozen_weights():
    head, users, target, blobs = bundle_fixture()
    reader = MagicMock(side_effect=blobs.__getitem__)
    encoder, cold = gnn_live.load_encoder(head, 5, reader)
    assert cold and reader.call_count == 2
    again, cold = gnn_live.load_encoder(head, 5, reader)
    assert again is encoder and not cold and reader.call_count == 2
    assert not any(parameter.requires_grad for parameter in encoder.parameters())
    embeddings, metadata = gnn_live.refresh_embeddings(head, users, [], target, encoder)
    assert set(embeddings) == {1, 2}
    assert metadata["contract"] == LIVE_ENCODING_CONTRACT


def test_corrupted_encoder_or_foreign_manifest_is_rejected():
    head, users, target, blobs = bundle_fixture()
    blobs["tenant_5/awards/gnn/gnn-v4-test/serving/encoder.pt"] += b"bad"
    with pytest.raises(ValueError, match="size/hash"):
        gnn_live.load_encoder(head, 5, blobs.__getitem__)
    with pytest.raises(ValueError, match="identity"):
        gnn_live.load_encoder(head, 6, lambda path: next(iter(blobs.values())))


def test_normal_live_path_does_not_read_cached_embeddings_and_persists_timings():
    head, users, target, blobs = bundle_fixture()
    encoder, _ = gnn_live.load_encoder(head, 5, blobs.__getitem__)
    head["_module"] = torch.nn.Linear(16 + len(NOMINATION_FEATURE_COLUMNS), 1).eval()
    policy = {"policy_id": 5, "policy_version": 1, "inference_enabled": True, "stale_embedding_days": 14,
              "thresholds": {"low": 25, "medium": 45, "high": 65, "critical": 85}}
    details = {"nomination_id": 100, "nominator_id": 1, "beneficiary_id": 2,
               "nomination_date": target["CreatedAt"], "amount": 100., "category_id": 1}
    with patch.object(gnn_check, "_get_head", return_value=head), \
         patch.object(gnn_check.db, "get_active_gnn_scoring_policy", return_value=policy), \
         patch.object(gnn_check.db, "get_gnn_live_graph_rows", return_value=(users, [])), \
         patch.object(gnn_check.db, "get_gnn_user_embeddings", side_effect=AssertionError("Stale fallback")), \
         patch("azure.storage.blob.BlobServiceClient"):
        result = gnn_check.assess_gnn(details, 5, {"serving_version": head["model_version"]})
    assert result["model_available"], result
    assert result["live_encoding"]["contract"] == LIVE_ENCODING_CONTRACT
    assert result["inference_timings"]["total_ms"] >= 0
    assert decision_contract.gnn_result(result)["live_encoding"] == result["live_encoding"]


def test_encoder_failure_is_unavailable_not_a_weekly_embedding_fallback():
    head, _users, target, _blobs = bundle_fixture()
    policy = {"policy_id": 5, "policy_version": 1, "inference_enabled": True,
              "thresholds": {"low": 25, "medium": 45, "high": 65, "critical": 85}}
    details = {"nomination_id": 100, "nominator_id": 1, "beneficiary_id": 2,
               "nomination_date": target["CreatedAt"]}
    with patch.object(gnn_check, "_get_head", return_value=head), \
         patch.object(gnn_check.db, "get_active_gnn_scoring_policy", return_value=policy), \
         patch.object(gnn_live, "load_encoder", side_effect=ValueError("Bad encoder")), \
         patch.object(gnn_check.db, "get_gnn_user_embeddings", side_effect=AssertionError("Stale fallback")), \
         patch("azure.storage.blob.BlobServiceClient"):
        result = gnn_check.assess_gnn(details, 5, {"serving_version": head["model_version"]})
    assert not result["model_available"]
    assert result["unavailable_reason"] == "INFERENCE_FAILED"


def test_live_target_features_match_the_training_builder():
    from integrity_engine.gnn.features import build_nomination_features
    from integrity_engine.gnn import causal_context_values
    import numpy as np
    head, _users, target, _blobs = bundle_fixture()
    target.update(Amount=123., CategoryId=1)
    history = [{**target, "NominationId": 1, "CreatedAt": datetime(2026, 9, 1, tzinfo=timezone.utc),
                "Status": "Paid"}]
    values = causal_context_values(history, target, window_days=365)
    actual, _ = gnn_check._nomination_feature_bundle(
        {"amount": 123., "category_id": 1, "nomination_date": target["CreatedAt"]}, head, values)
    expected = build_nomination_features([target], head["category_amount_stats"], target["CreatedAt"].date(),
                                         historical=False, context_rows=history, causal_window_days=365)
    np.testing.assert_allclose(actual, expected)
