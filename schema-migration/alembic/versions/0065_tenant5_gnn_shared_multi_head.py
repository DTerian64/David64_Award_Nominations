"""Activate GNN v4 shared-encoder training for synthetic tenant 5 only.

Deploy the v4-capable analytics job, integrity-check worker, extension, API,
and frontend before running this migration.  The migration changes the policy
read by the next analytics run; it does not replace the current serving model.

Revision ID: 0065
Revises: 0064
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None

TENANT_ID = 5
TENANT_NAME = "Synthetics Inc"
ACTOR = "migration:0065"

PATTERN_HEADS = {
    "RECIPROCAL": "reciprocal-v1",
    "RING": "ring-v1",
    "TEMPORAL_BURST": "temporal-burst-v1",
    "SUPER_NOMINATOR": "super-nominator-v1",
    "SUPER_BENEFICIARY": "super-beneficiary-v1",
    "BIPARTITE_DENSE_BLOCK": "bipartite-dense-block-v1",
}


def _v4_configuration(raw: str) -> str:
    configuration = json.loads(raw)
    if not isinstance(configuration, dict):
        raise ValueError("Tenant 5 GNN policy configuration must be an object")
    if (configuration.get("schema_version") != 3 or
            configuration.get("serving_mode") != "scenario_specialists"):
        raise ValueError("Tenant 5 must have an active v3 specialist policy")
    for key in ("model", "training", "artifacts", "architecture_selection", "score_routing"):
        if not isinstance(configuration.get(key), dict):
            raise ValueError(f"Tenant 5 GNN policy is missing {key}")
    if configuration["training"].get("rolling_fold_count", 0) < 3:
        raise ValueError("GNN v4 requires at least three temporal folds")

    configuration["schema_version"] = 4
    configuration["serving_mode"] = "shared_encoder_multi_head"
    configuration.pop("behavior_tracks", None)
    configuration.pop("aggregation", None)
    configuration["training"].update({
        "overall_loss_weight": 1.0,
        "pattern_total_loss_weight": 1.0,
    })
    configuration["architecture_selection"].update({
        "selection_metric": "validation_overall_pr_auc",
        "primary_metric": "validation_overall_pr_auc",
        "tie_breaker_metric": "validation_macro_pattern_pr_auc",
        "overall_tie_tolerance": 0.01,
        "minimum_graph_value_over_raw_mlp": 0.02,
        "minimum_message_passing_value_over_engineered_graph_mlp": 0.0,
        "maximum_holdout_inference_ms": 500.0,
        "maximum_overall_holdout_brier_score": 0.25,
    })
    configuration["pattern_heads"] = {
        key: {"enabled": True, "feature_contract": contract}
        for key, contract in PATTERN_HEADS.items()
    }
    configuration["pattern_admission"] = {
        "minimum_train_positives": 15,
        "minimum_validation_positives": 5,
        "minimum_holdout_positives": 5,
        "minimum_holdout_negatives": 100,
        "maximum_holdout_brier_score": 0.25,
        "maximum_validation_pr_auc_range": 0.5,
        "minimum_improvement_over_engineered_mlp": 0.0,
    }
    return json.dumps(configuration, separators=(",", ":"))


def upgrade() -> None:
    conn = op.get_bind()
    tenant = conn.execute(sa.text("""
        SELECT TenantName, is_synthetic
        FROM dbo.Tenants WHERE TenantId=:tenant_id
    """), {"tenant_id": TENANT_ID}).one_or_none()
    if tenant is None or tenant[0] != TENANT_NAME or not tenant[1]:
        raise RuntimeError("Tenant 5 is not the expected synthetic Synthetics Inc tenant")

    active = conn.execute(sa.text("""
        SELECT PolicyId, PolicyVersion, TrainingEnabled, InferenceEnabled,
               ConfigurationJson, ExplanationEnabled, ExplanationMinimumRisk
        FROM dbo.GNNScoringPolicies WITH (UPDLOCK, HOLDLOCK)
        WHERE TenantId=:tenant_id AND Status='ACTIVE'
    """), {"tenant_id": TENANT_ID}).one_or_none()
    if active is None:
        raise RuntimeError("Tenant 5 has no active GNN policy")
    current = json.loads(active[4])
    if (current.get("schema_version") == 4 and
            current.get("serving_mode") == "shared_encoder_multi_head"):
        return
    if not active[2]:
        raise RuntimeError("Tenant 5 GNN training is disabled; enable it before v4 activation")
    draft = conn.execute(sa.text("""
        SELECT 1 FROM dbo.GNNScoringPolicies
        WHERE TenantId=:tenant_id AND Status='DRAFT'
    """), {"tenant_id": TENANT_ID}).first()
    if draft is not None:
        raise RuntimeError("Tenant 5 has a draft GNN policy; resolve it before migration 0065")

    configuration = _v4_configuration(active[4])
    next_version = conn.execute(sa.text("""
        SELECT ISNULL(MAX(PolicyVersion), 0) + 1
        FROM dbo.GNNScoringPolicies WHERE TenantId=:tenant_id
    """), {"tenant_id": TENANT_ID}).scalar_one()
    retired = conn.execute(sa.text("""
        UPDATE dbo.GNNScoringPolicies
        SET Status='RETIRED', UpdatedAt=SYSUTCDATETIME(), UpdatedBy=:actor
        WHERE PolicyId=:policy_id AND TenantId=:tenant_id AND Status='ACTIVE'
    """), {"policy_id": int(active[0]), "tenant_id": TENANT_ID, "actor": ACTOR})
    if retired.rowcount != 1:
        raise RuntimeError("Tenant 5 active GNN policy changed during migration")
    conn.execute(sa.text("""
        INSERT INTO dbo.GNNScoringPolicies (
            TenantId, PolicyVersion, Status, TrainingEnabled, InferenceEnabled,
            ConfigurationJson, ExplanationEnabled, ExplanationMinimumRisk,
            CreatedBy, UpdatedBy, PublishedAt, PublishedBy
        ) VALUES (
            :tenant_id, :version, 'ACTIVE', :training_enabled, :inference_enabled,
            :configuration, :explanation_enabled, :explanation_minimum_risk,
            :actor, :actor, SYSUTCDATETIME(), :actor
        )
    """), {
        "tenant_id": TENANT_ID,
        "version": int(next_version),
        "training_enabled": bool(active[2]),
        "inference_enabled": bool(active[3]),
        "configuration": configuration,
        "explanation_enabled": bool(active[5]),
        "explanation_minimum_risk": active[6],
        "actor": ACTOR,
    })


def downgrade() -> None:
    conn = op.get_bind()
    active = conn.execute(sa.text("""
        SELECT PolicyId, PolicyVersion, PublishedBy, ConfigurationJson
        FROM dbo.GNNScoringPolicies WITH (UPDLOCK, HOLDLOCK)
        WHERE TenantId=:tenant_id AND Status='ACTIVE'
    """), {"tenant_id": TENANT_ID}).one_or_none()
    if active is None:
        raise RuntimeError("Tenant 5 has no active GNN policy to roll back")
    if active[2] != ACTOR:
        # Upgrade may have found an already-published v4 policy, or an admin
        # may have published a newer policy since. Neither is ours to undo.
        return
    current = json.loads(active[3])
    if (current.get("schema_version") != 4 or
            current.get("serving_mode") != "shared_encoder_multi_head"):
        raise RuntimeError("Tenant 5 active GNN policy is not migration 0065 v4")
    previous = conn.execute(sa.text("""
        SELECT PolicyId, ConfigurationJson
        FROM dbo.GNNScoringPolicies
        WHERE TenantId=:tenant_id AND Status='RETIRED' AND UpdatedBy=:actor
          AND PolicyVersion<:version
        ORDER BY PolicyVersion DESC
    """), {
        "tenant_id": TENANT_ID, "actor": ACTOR,
        "version": int(active[1]),
    }).first()
    if previous is None:
        raise RuntimeError("Tenant 5 prior GNN policy is missing")
    prior_configuration = json.loads(previous[1])
    if (prior_configuration.get("schema_version") != 3 or
            prior_configuration.get("serving_mode") != "scenario_specialists"):
        raise RuntimeError("Tenant 5 prior GNN policy is not v3")
    conn.execute(sa.text("""
        UPDATE dbo.GNNScoringPolicies
        SET Status='RETIRED', UpdatedAt=SYSUTCDATETIME(), UpdatedBy=:actor
        WHERE PolicyId=:policy_id
    """), {"policy_id": int(active[0]), "actor": f"{ACTOR}:downgrade"})
    conn.execute(sa.text("""
        UPDATE dbo.GNNScoringPolicies
        SET Status='ACTIVE', UpdatedAt=SYSUTCDATETIME(), UpdatedBy=:actor,
            PublishedAt=SYSUTCDATETIME(), PublishedBy=:actor
        WHERE PolicyId=:policy_id AND Status='RETIRED'
    """), {"policy_id": int(previous[0]), "actor": f"{ACTOR}:downgrade"})
