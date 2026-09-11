"""Add tenant-owned, versioned GNN training and scoring policies.

Revision ID: 0059
Revises: 0058

GNN tuning is application policy, not container infrastructure.  This table
replaces both Terraform-injected GNN tunables and the GNN subsection of
Tenants.integrity_config as the runtime source of truth.  Existing tenant JSON
is read while seeding version 1 so routing and explanation behaviour do not
change during deployment; the old JSON is retained only for rollback safety.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


DEFAULT_CANDIDATES = ["graphsage", "gcn", "gatv2"]


def _configuration(routing: dict) -> dict:
    return {
        "schema_version": 1,
        "model": {
            "hidden_dimension": 64,
            "embedding_dimension": 64,
        },
        "training": {
            "epochs": 300,
            "rolling_fold_count": 3,
            "window_days": 180,
            "minimum_training_samples": 300,
            "minimum_users": 50,
            "minimum_positive_labels_per_split": 10,
        },
        "artifacts": {
            "embedding_retention_days": 90,
            "stale_embedding_days": 14,
        },
        "architecture_selection": {
            "candidate_architectures": DEFAULT_CANDIDATES,
            "selection_metric": "holdout_pr_auc",
            "minimum_improvement_over_mlp": 0.02,
            "incumbent_tie_tolerance": 0.01,
            "minimum_eligible_graph_candidates": 2,
        },
        "score_routing": {
            "low_threshold": float(routing.get("low_threshold", 25)),
            "medium_threshold": float(routing.get("medium_threshold", 45)),
            "high_threshold": float(routing.get("high_threshold", 65)),
            "critical_threshold": float(routing.get("critical_threshold", 85)),
        },
    }


def _table_exists(table: str) -> bool:
    return bool(op.get_bind().execute(sa.text("""
        SELECT 1 FROM sys.tables WHERE object_id = OBJECT_ID(:table)
    """), {"table": f"dbo.{table}"}).scalar())


def _parse_json(raw) -> dict:
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _seed_active_policies() -> None:
    conn = op.get_bind()
    tenants = conn.execute(
        sa.text("SELECT TenantId, integrity_config FROM dbo.Tenants")
    ).fetchall()
    for tenant_id, raw_config in tenants:
        exists = conn.execute(sa.text("""
            SELECT 1 FROM dbo.GNNScoringPolicies WHERE TenantId=:tenant_id
        """), {"tenant_id": tenant_id}).fetchone()
        if exists:
            continue

        config = _parse_json(raw_config)
        gnn = config.get("gnn") if isinstance(config.get("gnn"), dict) else {}
        routing = (
            gnn.get("score_routing")
            if isinstance(gnn.get("score_routing"), dict)
            else {}
        )
        explanation = (
            gnn.get("explanation")
            if isinstance(gnn.get("explanation"), dict)
            else {}
        )
        actor = "migration:0059"
        conn.execute(sa.text("""
            INSERT INTO dbo.GNNScoringPolicies (
                TenantId, PolicyVersion, Status,
                TrainingEnabled, InferenceEnabled,
                ConfigurationJson,
                ExplanationEnabled, ExplanationMinimumRisk,
                CreatedBy, UpdatedBy, PublishedAt, PublishedBy
            ) VALUES (
                :tenant_id, 1, 'ACTIVE',
                1, :inference_enabled,
                :configuration,
                :explanation_enabled, :minimum_risk,
                :actor, :actor, SYSUTCDATETIME(), :actor
            )
        """), {
            "tenant_id": tenant_id,
            "inference_enabled": int(bool(gnn.get("inference_enabled", True))),
            "configuration": json.dumps(
                _configuration(routing), separators=(",", ":")
            ),
            "explanation_enabled": int(bool(explanation.get("enabled", False))),
            "minimum_risk": str(explanation.get("minimum_risk", "MEDIUM")).upper(),
            "actor": actor,
        })


def upgrade() -> None:
    if _table_exists("GNNScoringPolicies"):
        return
    op.execute("""
        CREATE TABLE dbo.GNNScoringPolicies (
            PolicyId INT IDENTITY(1,1) NOT NULL,
            TenantId INT NOT NULL,
            PolicyVersion INT NOT NULL,
            Status VARCHAR(10) NOT NULL,
            TrainingEnabled BIT NOT NULL,
            InferenceEnabled BIT NOT NULL,
            ConfigurationJson NVARCHAR(MAX) NOT NULL,
            ExplanationEnabled BIT NOT NULL,
            ExplanationMinimumRisk VARCHAR(10) NOT NULL,
            CreatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
            CreatedBy NVARCHAR(256) NOT NULL,
            UpdatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
            UpdatedBy NVARCHAR(256) NOT NULL,
            PublishedAt DATETIME2 NULL,
            PublishedBy NVARCHAR(256) NULL,
            CONSTRAINT PK_GNNScoringPolicies PRIMARY KEY (PolicyId),
            CONSTRAINT FK_GNNScoringPolicies_Tenants FOREIGN KEY (TenantId)
                REFERENCES dbo.Tenants(TenantId),
            CONSTRAINT UQ_GNNScoringPolicies_Version
                UNIQUE (TenantId, PolicyVersion),
            CONSTRAINT CK_GNNScoringPolicies_Status
                CHECK (Status IN ('DRAFT','ACTIVE','RETIRED')),
            CONSTRAINT CK_GNNScoringPolicies_ConfigurationJson
                CHECK (ISJSON(ConfigurationJson) = 1),
            CONSTRAINT CK_GNNScoringPolicies_ExplanationRisk CHECK (
                ExplanationMinimumRisk IN ('NONE','LOW','MEDIUM','HIGH','CRITICAL')
            )
        );
    """)
    op.execute("""
        CREATE UNIQUE INDEX UX_GNNScoringPolicies_Active
        ON dbo.GNNScoringPolicies(TenantId) WHERE Status='ACTIVE';
    """)
    op.execute("""
        CREATE UNIQUE INDEX UX_GNNScoringPolicies_Draft
        ON dbo.GNNScoringPolicies(TenantId) WHERE Status='DRAFT';
    """)
    _seed_active_policies()


def downgrade() -> None:
    if _table_exists("GNNScoringPolicies"):
        op.execute("DROP TABLE dbo.GNNScoringPolicies;")
