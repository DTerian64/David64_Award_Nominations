"""Activate the GNN scenario-specialist serving contract for synthetic tenants.

Real tenants keep their current v2 policy.  Synthetic tenants receive a new
immutable active policy version so the rollout is explicit and reversible.

Revision ID: 0064
Revises: 0063
Create Date: 2026-09-18
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


_TRACKS = {
    "RECIPROCAL": "reciprocal-v1",
    "RING": "ring-v1",
    "TEMPORAL_BURST": "temporal-burst-v1",
    "SUPER_NOMINATOR": "super-nominator-v1",
    "SUPER_BENEFICIARY": "super-beneficiary-v1",
    "BIPARTITE_DENSE_BLOCK": "bipartite-dense-block-v1",
}


def _track(feature_contract: str) -> dict:
    return {
        "enabled": True,
        "candidate_architectures": ["graphsage", "gcn", "gatv2"],
        "feature_contract": feature_contract,
        "minimum_train_positives": 15,
        "minimum_train_negatives": 100,
        "minimum_holdout_positives": 5,
        "minimum_holdout_negatives": 100,
        "minimum_evaluable_temporal_folds": 2,
        "maximum_fold_pr_auc_range": 0.40,
        "minimum_improvement_over_mlp": 0.02,
        "maximum_holdout_brier_score": 0.25,
        "maximum_holdout_inference_ms": 1000.0,
        "incumbent_refresh_tolerance": 0.01,
        "architecture_switch_tolerance": 0.01,
    }


def _v3_configuration(raw: str) -> str:
    configuration = json.loads(raw)
    if not isinstance(configuration, dict):
        raise ValueError("GNN ConfigurationJson must be an object")
    configuration["schema_version"] = 3
    configuration["serving_mode"] = "scenario_specialists"
    configuration["behavior_tracks"] = {
        key: _track(contract) for key, contract in _TRACKS.items()
    }
    configuration["aggregation"] = {
        "method": "maximum_calibrated_probability",
        "mixed_minimum_specialists": 2,
    }
    return json.dumps(configuration, separators=(",", ":"))


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("""
        SELECT p.PolicyId, p.TenantId, p.PolicyVersion,
               p.TrainingEnabled, p.InferenceEnabled, p.ConfigurationJson,
               p.ExplanationEnabled, p.ExplanationMinimumRisk
        FROM dbo.GNNScoringPolicies p
        JOIN dbo.Tenants t ON t.TenantId = p.TenantId
        WHERE p.Status='ACTIVE' AND t.is_synthetic=1
        ORDER BY p.TenantId
    """)).fetchall()
    actor = "migration:0064"
    for row in rows:
        configuration = json.loads(row[5])
        if (
            isinstance(configuration, dict)
            and configuration.get("schema_version") == 3
            and configuration.get("serving_mode") == "scenario_specialists"
        ):
            continue
        next_version = int(conn.execute(sa.text("""
            SELECT ISNULL(MAX(PolicyVersion), 0) + 1
            FROM dbo.GNNScoringPolicies WHERE TenantId=:tenant_id
        """), {"tenant_id": int(row[1])}).scalar_one())
        conn.execute(sa.text("""
            UPDATE dbo.GNNScoringPolicies
            SET Status='RETIRED', UpdatedAt=SYSUTCDATETIME(), UpdatedBy=:actor
            WHERE PolicyId=:policy_id
        """), {"policy_id": int(row[0]), "actor": actor})
        conn.execute(sa.text("""
            INSERT INTO dbo.GNNScoringPolicies (
                TenantId, PolicyVersion, Status,
                TrainingEnabled, InferenceEnabled, ConfigurationJson,
                ExplanationEnabled, ExplanationMinimumRisk,
                CreatedBy, UpdatedBy, PublishedAt, PublishedBy
            ) VALUES (
                :tenant_id, :version, 'ACTIVE',
                :training_enabled, :inference_enabled, :configuration,
                :explanation_enabled, :minimum_risk,
                :actor, :actor, SYSUTCDATETIME(), :actor
            )
        """), {
            "tenant_id": int(row[1]),
            "version": next_version,
            "training_enabled": bool(row[3]),
            "inference_enabled": bool(row[4]),
            "configuration": _v3_configuration(row[5]),
            "explanation_enabled": bool(row[6]),
            "minimum_risk": row[7],
            "actor": actor,
        })


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("""
        SELECT PolicyId, TenantId
        FROM dbo.GNNScoringPolicies
        WHERE Status='ACTIVE'
          AND JSON_VALUE(ConfigurationJson, '$.serving_mode')='scenario_specialists'
    """)).fetchall()
    actor = "migration:0064:downgrade"
    for policy_id, tenant_id in rows:
        conn.execute(sa.text("""
            UPDATE dbo.GNNScoringPolicies
            SET Status='RETIRED', UpdatedAt=SYSUTCDATETIME(), UpdatedBy=:actor
            WHERE PolicyId=:policy_id
        """), {"policy_id": int(policy_id), "actor": actor})
        prior = conn.execute(sa.text("""
            SELECT TOP 1 PolicyId FROM dbo.GNNScoringPolicies
            WHERE TenantId=:tenant_id AND Status='RETIRED'
              AND JSON_VALUE(ConfigurationJson, '$.schema_version')='1'
            ORDER BY PolicyVersion DESC
        """), {"tenant_id": int(tenant_id)}).scalar_one_or_none()
        if prior is not None:
            conn.execute(sa.text("""
                UPDATE dbo.GNNScoringPolicies
                SET Status='ACTIVE', UpdatedAt=SYSUTCDATETIME(), UpdatedBy=:actor,
                    PublishedAt=SYSUTCDATETIME(), PublishedBy=:actor
                WHERE PolicyId=:policy_id
            """), {"policy_id": int(prior), "actor": actor})
