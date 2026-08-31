"""Remove obsolete GNN shadow-mode schema and tenant configuration.

Revision ID: 0043
Revises: 0042
Create Date: 2026-08-23

This revision is intentionally idempotent. It supports databases that applied
the earlier form of 0042 as well as fresh databases where 0042 already performed
the cleanup. An available GNN is an equal routing component; an unavailable GNN
contributes no opinion.
"""

import json

import sqlalchemy as sa
from alembic import op


revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :name"
        ),
        {"name": name},
    ).fetchone() is not None


def _column_exists(table: str, column: str) -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :table AND COLUMN_NAME = :column"
        ),
        {"table": table, "column": column},
    ).fetchone() is not None


def _index_exists(table: str, index: str) -> bool:
    return op.get_bind().execute(
        sa.text("SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(:table) AND name = :index"),
        {"table": f"dbo.{table}", "index": index},
    ).fetchone() is not None


def _constraint_exists(table: str, constraint: str) -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM sys.objects "
            "WHERE parent_object_id = OBJECT_ID(:table) AND name = :constraint"
        ),
        {"table": f"dbo.{table}", "constraint": constraint},
    ).fetchone() is not None


def _remove_config_mode() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT TenantId, integrity_config FROM dbo.Tenants")
    ).fetchall()
    for tenant_id, raw in rows:
        try:
            config = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(
                f"Tenant {tenant_id} has invalid integrity_config JSON; "
                "refusing to overwrite it while removing obsolete gnn.mode."
            ) from exc
        if not isinstance(config, dict):
            raise ValueError(f"Tenant {tenant_id} integrity_config must be a JSON object.")
        gnn = config.get("gnn")
        if not isinstance(gnn, dict) or "mode" not in gnn:
            continue
        del gnn["mode"]
        config["gnn"] = gnn
        conn.execute(
            sa.text(
                "UPDATE dbo.Tenants SET integrity_config = :config "
                "WHERE TenantId = :tenant_id"
            ),
            {"config": json.dumps(config, separators=(",", ":")), "tenant_id": tenant_id},
        )


def upgrade() -> None:
    _remove_config_mode()

    if _table_exists("GNN_FraudScores") and _column_exists("GNN_FraudScores", "ScoringMode"):
        if _index_exists("GNN_FraudScores", "IX_GNN_FraudScores_Nomination"):
            op.execute("DROP INDEX IX_GNN_FraudScores_Nomination ON dbo.GNN_FraudScores;")
        if _constraint_exists("GNN_FraudScores", "CK_GNN_FraudScores_ScoringMode"):
            op.execute(
                "ALTER TABLE dbo.GNN_FraudScores "
                "DROP CONSTRAINT CK_GNN_FraudScores_ScoringMode;"
            )
        if _constraint_exists("GNN_FraudScores", "DF_GNN_FraudScores_ScoringMode"):
            op.execute(
                "ALTER TABLE dbo.GNN_FraudScores "
                "DROP CONSTRAINT DF_GNN_FraudScores_ScoringMode;"
            )
        op.execute("ALTER TABLE dbo.GNN_FraudScores DROP COLUMN ScoringMode;")

    if (_table_exists("GNN_FraudScores")
            and not _index_exists("GNN_FraudScores", "IX_GNN_FraudScores_Nomination")):
        op.execute("""
            CREATE INDEX IX_GNN_FraudScores_Nomination
                ON dbo.GNN_FraudScores (NominationId, CreatedAt DESC)
                INCLUDE (FraudScore, RiskLevel, ModelVersion);
        """)

    if (_table_exists("FraudDecisionResults")
            and _column_exists("FraudDecisionResults", "GnnScoringMode")):
        if _constraint_exists("FraudDecisionResults", "CK_FraudDecisionResults_GnnScoringMode"):
            op.execute(
                "ALTER TABLE dbo.FraudDecisionResults "
                "DROP CONSTRAINT CK_FraudDecisionResults_GnnScoringMode;"
            )
        op.execute("ALTER TABLE dbo.FraudDecisionResults DROP COLUMN GnnScoringMode;")


def downgrade() -> None:
    # The preceding revision's current schema also has no routing-mode columns.
    # Do not recreate obsolete operational state during downgrade.
    pass
