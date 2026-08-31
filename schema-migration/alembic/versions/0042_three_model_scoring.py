"""Add graph component scores, fused decisions, and the GNN routing mode.

Revision ID: 0042
Revises: 0041
Create Date: 2026-08-22

The component tables remain separate: P2P_FraudScores is the Random Forest,
Graph_FraudScores is graph analytics, and GNN_FraudScores is the GNN.  The
decision table records their opinions without replacing any component score.
"""

import json

import sqlalchemy as sa
from alembic import op


revision = "0042"
down_revision = "0041"
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


def _seed_gnn_mode() -> None:
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
                "refusing to overwrite it while adding gnn.mode."
            ) from exc
        if not isinstance(config, dict):
            raise ValueError(f"Tenant {tenant_id} integrity_config must be a JSON object.")
        gnn = config.get("gnn")
        if not isinstance(gnn, dict):
            gnn = {}
        if "mode" in gnn:
            continue
        gnn["mode"] = "shadow"
        config["gnn"] = gnn
        conn.execute(
            sa.text(
                "UPDATE dbo.Tenants SET integrity_config = :config "
                "WHERE TenantId = :tenant_id"
            ),
            {"config": json.dumps(config, separators=(",", ":")), "tenant_id": tenant_id},
        )


def upgrade() -> None:
    _seed_gnn_mode()

    if not _table_exists("Graph_FraudScores"):
        op.execute("""
            CREATE TABLE dbo.Graph_FraudScores (
                GraphScoreId       INT IDENTITY(1,1) NOT NULL,
                NominationId       INT NOT NULL,
                GraphScore         INT NOT NULL,
                RiskLevel          VARCHAR(20) NOT NULL,
                GraphFlags         NVARCHAR(1000) NULL,
                SnapshotAsOfDate   DATE NOT NULL,
                ScoredBy           NVARCHAR(256) NULL,
                CreatedAt          DATETIME2 NOT NULL
                    CONSTRAINT DF_Graph_FraudScores_CreatedAt DEFAULT SYSUTCDATETIME(),
                UpdatedAt          DATETIME2 NOT NULL
                    CONSTRAINT DF_Graph_FraudScores_UpdatedAt DEFAULT SYSUTCDATETIME(),
                CONSTRAINT PK_Graph_FraudScores PRIMARY KEY CLUSTERED (GraphScoreId),
                CONSTRAINT FK_Graph_FraudScores_Nominations
                    FOREIGN KEY (NominationId) REFERENCES dbo.Nominations (NominationId),
                CONSTRAINT UQ_Graph_FraudScores_Nomination_Snapshot
                    UNIQUE (NominationId, SnapshotAsOfDate),
                CONSTRAINT CK_Graph_FraudScores_GraphScore
                    CHECK (GraphScore BETWEEN 0 AND 100),
                CONSTRAINT CK_Graph_FraudScores_RiskLevel
                    CHECK (RiskLevel IN ('NONE','LOW','MEDIUM','HIGH','CRITICAL'))
            );
        """)
        op.execute("""
            CREATE INDEX IX_Graph_FraudScores_RiskLevel
                ON dbo.Graph_FraudScores (RiskLevel, SnapshotAsOfDate DESC);
        """)

    if not _table_exists("FraudDecisionResults"):
        op.execute("""
            CREATE TABLE dbo.FraudDecisionResults (
                NominationId       INT NOT NULL,
                PolicyVersion      VARCHAR(40) NOT NULL,
                RfAvailable        BIT NOT NULL,
                RfScore            INT NULL,
                RfRiskLevel        VARCHAR(20) NULL,
                GraphAvailable     BIT NOT NULL,
                GraphScore         INT NULL,
                GraphRiskLevel     VARCHAR(20) NULL,
                GnnAvailable       BIT NOT NULL,
                GnnScore           INT NULL,
                GnnRiskLevel       VARCHAR(20) NULL,
                GnnScoringMode     VARCHAR(10) NOT NULL,
                FinalScore         INT NOT NULL,
                FinalRiskLevel     VARCHAR(20) NOT NULL,
                DecisiveModels     VARCHAR(100) NULL,
                ScoredBy           NVARCHAR(256) NULL,
                CreatedAt          DATETIME2 NOT NULL
                    CONSTRAINT DF_FraudDecisionResults_CreatedAt DEFAULT SYSUTCDATETIME(),
                UpdatedAt          DATETIME2 NOT NULL
                    CONSTRAINT DF_FraudDecisionResults_UpdatedAt DEFAULT SYSUTCDATETIME(),
                CONSTRAINT PK_FraudDecisionResults PRIMARY KEY CLUSTERED (NominationId),
                CONSTRAINT FK_FraudDecisionResults_Nominations
                    FOREIGN KEY (NominationId) REFERENCES dbo.Nominations (NominationId),
                CONSTRAINT CK_FraudDecisionResults_GnnScoringMode
                    CHECK (GnnScoringMode IN ('shadow','active')),
                CONSTRAINT CK_FraudDecisionResults_FinalScore
                    CHECK (FinalScore BETWEEN 0 AND 100),
                CONSTRAINT CK_FraudDecisionResults_FinalRiskLevel
                    CHECK (FinalRiskLevel IN ('UNKNOWN','NONE','LOW','MEDIUM','HIGH','CRITICAL'))
            );
        """)
        op.execute("""
            CREATE INDEX IX_FraudDecisionResults_FinalRisk
                ON dbo.FraudDecisionResults (FinalRiskLevel, UpdatedAt DESC);
        """)


def downgrade() -> None:
    if _table_exists("FraudDecisionResults"):
        op.execute("DROP TABLE dbo.FraudDecisionResults;")
    if _table_exists("Graph_FraudScores"):
        op.execute("DROP TABLE dbo.Graph_FraudScores;")
    # gnn.mode is operational tenant data and may have been changed to active;
    # do not erase it during a schema downgrade.
