"""Tenant-scoped GNN v4 policy activation contract."""

import importlib.util
import json
from pathlib import Path

import pytest


def _path() -> Path:
    return Path(__file__).parents[1] / "alembic/versions/0065_tenant5_gnn_shared_multi_head.py"


def _load():
    spec = importlib.util.spec_from_file_location("migration_0065", _path())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _v3() -> dict:
    return {
        "schema_version": 3,
        "serving_mode": "scenario_specialists",
        "model": {"hidden_dimension": 32, "embedding_dimension": 48},
        "training": {
            "epochs": 200, "rolling_fold_count": 3, "window_days": 365,
            "minimum_training_samples": 300, "minimum_users": 50,
            "minimum_positive_labels_per_split": 10,
        },
        "artifacts": {"embedding_retention_days": 90, "stale_embedding_days": 14},
        "architecture_selection": {
            "candidate_architectures": ["graphsage", "gcn", "gatv2"],
            "selection_metric": "holdout_pr_auc",
            "minimum_improvement_over_mlp": 0.02,
            "incumbent_tie_tolerance": 0.01,
            "minimum_eligible_graph_candidates": 2,
        },
        "score_routing": {
            "low_threshold": 25, "medium_threshold": 45,
            "high_threshold": 65, "critical_threshold": 85,
        },
        "behavior_tracks": {"RING": {"enabled": True}},
        "aggregation": {"method": "maximum_calibrated_probability"},
    }


def test_v4_policy_clones_tenant_settings_and_replaces_v3_tracks():
    migration = _load()
    assert migration.revision == "0065"
    assert migration.down_revision == "0064"
    assert migration.TENANT_ID == 5
    assert migration.TENANT_NAME == "Synthetics Inc"

    original = _v3()
    result = json.loads(migration._v4_configuration(json.dumps(original)))
    assert result["schema_version"] == 4
    assert result["serving_mode"] == "shared_encoder_multi_head"
    assert result["model"] == original["model"]
    assert result["training"]["window_days"] == 365
    assert result["score_routing"] == original["score_routing"]
    assert result["artifacts"] == original["artifacts"]
    assert "behavior_tracks" not in result
    assert "aggregation" not in result
    assert result["architecture_selection"]["primary_metric"] == "validation_overall_pr_auc"
    assert result["architecture_selection"]["minimum_graph_value_over_raw_mlp"] == 0.02
    assert result["architecture_selection"]["minimum_message_passing_value_over_engineered_graph_mlp"] == 0.0
    assert set(result["pattern_heads"]) == set(migration.PATTERN_HEADS)
    assert all(head["enabled"] for head in result["pattern_heads"].values())


@pytest.mark.parametrize("change", [
    {"schema_version": 1},
    {"serving_mode": "single_winner_v2"},
    {"training": {"rolling_fold_count": 2}},
])
def test_v4_policy_rejects_unexpected_source_contract(change):
    original = _v3()
    original.update(change)
    with pytest.raises(ValueError):
        _load()._v4_configuration(json.dumps(original))


def test_migration_is_bounded_and_reversible():
    source = _path().read_text(encoding="utf-8")
    assert "TenantId=:tenant_id" in source
    assert "is_synthetic" in source
    assert "Status='DRAFT'" in source
    assert "PublishedBy" in source
    assert "def downgrade()" in source
