"""Add versioned, continuous Graph Analytics scoring.

Revision ID: 0049
Revises: 0048
Create Date: 2026-08-31

IntegrityComponentStatus is the completed-run marker, including successful
runs with zero findings. This migration deliberately adds no second run table.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


_PATTERNS = {
    "Ring": (True, 35, ["nominator", "beneficiary"], {
        "amount_reference": 10000, "exposure_weight": 35,
        "repeat_weight": 15, "compactness_weight": 15,
    }),
    "SuperNominator": (True, 35, ["nominator"], {
        "minimum_count": 5, "standard_deviations": 2.0,
        "median_multiplier": 3.0, "excess_weight": 30,
        "volume_weight": 20, "exposure_weight": 15,
        "amount_reference": 10000,
    }),
    "Desert": (False, 25, ["beneficiary"], {
        "minimum_team_size": 3, "team_size_reference": 10,
        "team_size_weight": 25,
    }),
    "CopyPaste": (True, 35, ["nominator"], {
        "similarity_threshold": 0.92, "minimum_cluster_size": 3,
        "similarity_weight": 35, "cluster_size_weight": 20,
        "exposure_weight": 10, "cluster_size_reference": 8,
        "amount_reference": 10000,
    }),
    "TransactionalLanguage": (True, 40, ["nominator"], {
        "minimum_hits": 2, "hit_reference": 6, "hit_weight": 45,
        "exposure_weight": 15, "amount_reference": 5000,
    }),
    "HiddenCandidate": (False, 30, ["beneficiary"], {
        "minimum_mentions": 5, "mention_reference": 15,
        "mention_weight": 40,
    }),
}


def _exists(kind: str, name: str, table: str | None = None) -> bool:
    conn = op.get_bind()
    if kind == "table":
        query = (
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=:name"
        )
        params = {"name": name}
    else:
        query = (
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=:table AND COLUMN_NAME=:name"
        )
        params = {"table": table, "name": name}
    return conn.execute(sa.text(query), params).fetchone() is not None


def _add_column(table: str, column: str, definition: str) -> None:
    if not _exists("column", column, table):
        op.execute(f"ALTER TABLE dbo.{table} ADD {column} {definition};")


def _constraint_exists(table: str, constraint: str) -> bool:
    return op.get_bind().execute(sa.text("""
        SELECT 1
        FROM sys.check_constraints c
        JOIN sys.tables t ON t.object_id=c.parent_object_id
        JOIN sys.schemas s ON s.schema_id=t.schema_id
        WHERE s.name='dbo' AND t.name=:table AND c.name=:constraint
    """), {"table": table, "constraint": constraint}).fetchone() is not None


def _drop_graph_score_constraint() -> None:
    if _constraint_exists(
        "Graph_FraudScores", "CK_Graph_FraudScores_GraphScore"
    ):
        op.execute("""
            ALTER TABLE dbo.Graph_FraudScores
            DROP CONSTRAINT CK_Graph_FraudScores_GraphScore;
        """)


def _create_graph_score_constraint() -> None:
    if not _constraint_exists(
        "Graph_FraudScores", "CK_Graph_FraudScores_GraphScore"
    ):
        op.execute("""
            ALTER TABLE dbo.Graph_FraudScores
            ADD CONSTRAINT CK_Graph_FraudScores_GraphScore
            CHECK (GraphScore BETWEEN 0 AND 100);
        """)


def _seed_baseline_policies() -> None:
    conn = op.get_bind()
    tenants = conn.execute(
        sa.text("SELECT TenantId, integrity_config FROM dbo.Tenants")
    ).fetchall()
    for tenant_id, raw_config in tenants:
        if conn.execute(
            sa.text("SELECT 1 FROM dbo.GraphScoringPolicies WHERE TenantId=:tid"),
            {"tid": tenant_id},
        ).fetchone():
            continue
        try:
            config = json.loads(raw_config) if raw_config else {}
        except (json.JSONDecodeError, TypeError):
            config = {}
        graph = config.get("graph", {}) if isinstance(config, dict) else {}
        routing = graph.get("score_routing", {}) if isinstance(graph, dict) else {}
        graph_pattern = config.get("graph_pattern", {}) if isinstance(config, dict) else {}
        values = {
            "tid": tenant_id,
            "low": int(routing.get("low_threshold", 25)),
            "medium": int(routing.get("medium_threshold", 50)),
            "high": int(routing.get("high_threshold", 75)),
            "critical": int(routing.get("critical_threshold", 100)),
            "window": int(graph_pattern.get("detection_window_days", 365)),
            "actor": "migration:0049",
        }
        policy_id = conn.execute(sa.text("""
            INSERT INTO dbo.GraphScoringPolicies (
                TenantId, PolicyVersion, Status, ScoringStrategy,
                LowThreshold, MediumThreshold, HighThreshold, CriticalThreshold,
                DetectionWindowDays, SnapshotMaxAgeDays,
                CreatedBy, UpdatedBy, PublishedBy, PublishedAt
            ) OUTPUT INSERTED.PolicyId
            VALUES (
                :tid, 1, 'ACTIVE', 'MAX_RELEVANT_FINDING',
                :low, :medium, :high, :critical, :window, 14,
                :actor, :actor, :actor, SYSUTCDATETIME()
            )
        """), values).scalar_one()
        for pattern_type, (routing_enabled, base, roles, parameters) in _PATTERNS.items():
            conn.execute(sa.text("""
                INSERT INTO dbo.GraphScoringPatternParameters (
                    PolicyId, PatternType, Enabled, EnabledForRouting,
                    ApplicableRolesJson, BaseScore, MinimumScore, MaximumScore,
                    ParametersJson, CreatedBy, UpdatedBy
                ) VALUES (
                    :policy_id, :pattern, 1, :routing, :roles,
                    :base, 0, 100, :parameters, :actor, :actor
                )
            """), {
                "policy_id": policy_id, "pattern": pattern_type,
                "routing": int(routing_enabled), "base": base,
                "roles": json.dumps(roles, separators=(",", ":")),
                "parameters": json.dumps(parameters, separators=(",", ":")),
                "actor": "migration:0049",
            })


def upgrade() -> None:
    # SQL Server will not alter a column while a check constraint depends on it.
    _drop_graph_score_constraint()
    op.execute(
        "ALTER TABLE dbo.Graph_FraudScores ALTER COLUMN GraphScore DECIMAL(5,2) NOT NULL;"
    )
    _create_graph_score_constraint()
    op.execute(
        "ALTER TABLE dbo.FraudDecisionResults ALTER COLUMN GraphScore DECIMAL(5,2) NULL;"
    )
    _add_column("GraphPatternFindings", "FindingScore", "DECIMAL(5,2) NULL")
    _add_column("GraphPatternFindings", "ScoringPolicyVersion", "INT NULL")
    _add_column("GraphPatternFindings", "ScoreComponentsJson", "NVARCHAR(MAX) NULL")
    _add_column("UserGraphFlags", "FindingsJson", "NVARCHAR(MAX) NULL")
    _add_column("Graph_FraudScores", "WinningFindingHash", "VARCHAR(64) NULL")
    _add_column("Graph_FraudScores", "WinningPatternType", "VARCHAR(50) NULL")
    _add_column("Graph_FraudScores", "ScoringStrategy", "VARCHAR(40) NULL")
    _add_column("Graph_FraudScores", "ScoringPolicyVersion", "INT NULL")
    _add_column("Graph_FraudScores", "SnapshotRunId", "VARCHAR(64) NULL")

    if not _exists("table", "GraphScoringPolicies"):
        op.execute("""
            CREATE TABLE dbo.GraphScoringPolicies (
                PolicyId INT IDENTITY(1,1) NOT NULL,
                TenantId INT NOT NULL,
                PolicyVersion INT NOT NULL,
                Status VARCHAR(10) NOT NULL,
                ScoringStrategy VARCHAR(40) NOT NULL,
                LowThreshold DECIMAL(5,2) NOT NULL,
                MediumThreshold DECIMAL(5,2) NOT NULL,
                HighThreshold DECIMAL(5,2) NOT NULL,
                CriticalThreshold DECIMAL(5,2) NOT NULL,
                DetectionWindowDays INT NOT NULL,
                SnapshotMaxAgeDays INT NOT NULL,
                CreatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                CreatedBy NVARCHAR(256) NOT NULL,
                UpdatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                UpdatedBy NVARCHAR(256) NOT NULL,
                PublishedAt DATETIME2 NULL,
                PublishedBy NVARCHAR(256) NULL,
                CONSTRAINT PK_GraphScoringPolicies PRIMARY KEY (PolicyId),
                CONSTRAINT FK_GraphScoringPolicies_Tenants FOREIGN KEY (TenantId)
                    REFERENCES dbo.Tenants(TenantId),
                CONSTRAINT UQ_GraphScoringPolicies_Version
                    UNIQUE (TenantId, PolicyVersion),
                CONSTRAINT CK_GraphScoringPolicies_Status
                    CHECK (Status IN ('DRAFT','ACTIVE','RETIRED')),
                CONSTRAINT CK_GraphScoringPolicies_Strategy
                    CHECK (ScoringStrategy='MAX_RELEVANT_FINDING'),
                CONSTRAINT CK_GraphScoringPolicies_Thresholds CHECK (
                    LowThreshold BETWEEN 0 AND 100 AND
                    MediumThreshold BETWEEN LowThreshold AND 100 AND
                    HighThreshold BETWEEN MediumThreshold AND 100 AND
                    CriticalThreshold BETWEEN HighThreshold AND 100
                ),
                CONSTRAINT CK_GraphScoringPolicies_Windows
                    CHECK (DetectionWindowDays > 0 AND SnapshotMaxAgeDays > 0)
            );
        """)
        op.execute("""
            CREATE UNIQUE INDEX UX_GraphScoringPolicies_Active
            ON dbo.GraphScoringPolicies(TenantId) WHERE Status='ACTIVE';
        """)
        op.execute("""
            CREATE UNIQUE INDEX UX_GraphScoringPolicies_Draft
            ON dbo.GraphScoringPolicies(TenantId) WHERE Status='DRAFT';
        """)

    if not _exists("table", "GraphScoringPatternParameters"):
        op.execute("""
            CREATE TABLE dbo.GraphScoringPatternParameters (
                PatternParameterId INT IDENTITY(1,1) NOT NULL,
                PolicyId INT NOT NULL,
                PatternType VARCHAR(50) NOT NULL,
                Enabled BIT NOT NULL,
                EnabledForRouting BIT NOT NULL,
                ApplicableRolesJson NVARCHAR(500) NOT NULL,
                BaseScore DECIMAL(5,2) NOT NULL,
                MinimumScore DECIMAL(5,2) NOT NULL,
                MaximumScore DECIMAL(5,2) NOT NULL,
                ParametersJson NVARCHAR(MAX) NOT NULL,
                CreatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                CreatedBy NVARCHAR(256) NOT NULL,
                UpdatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                UpdatedBy NVARCHAR(256) NOT NULL,
                CONSTRAINT PK_GraphScoringPatternParameters
                    PRIMARY KEY (PatternParameterId),
                CONSTRAINT FK_GraphScoringPatternParameters_Policy
                    FOREIGN KEY (PolicyId) REFERENCES dbo.GraphScoringPolicies(PolicyId)
                    ON DELETE CASCADE,
                CONSTRAINT UQ_GraphScoringPatternParameters
                    UNIQUE (PolicyId, PatternType),
                CONSTRAINT CK_GraphScoringPatternParameters_Scores CHECK (
                    BaseScore BETWEEN 0 AND 100 AND
                    MinimumScore BETWEEN 0 AND 100 AND
                    MaximumScore BETWEEN MinimumScore AND 100
                ),
                CONSTRAINT CK_GraphScoringPatternParameters_Roles
                    CHECK (ISJSON(ApplicableRolesJson)=1),
                CONSTRAINT CK_GraphScoringPatternParameters_Parameters
                    CHECK (ISJSON(ParametersJson)=1)
            );
        """)

    if not _exists("table", "GraphScoringChangeRequests"):
        op.execute("""
            CREATE TABLE dbo.GraphScoringChangeRequests (
                RequestId INT IDENTITY(1,1) NOT NULL,
                TenantId INT NOT NULL,
                PolicyId INT NULL,
                ResolvedPolicyId INT NULL,
                PatternType VARCHAR(50) NULL,
                RequestText NVARCHAR(2000) NOT NULL,
                SuggestedParametersJson NVARCHAR(MAX) NULL,
                SupportingNominationIdsJson NVARCHAR(MAX) NULL,
                Status VARCHAR(20) NOT NULL DEFAULT 'REQUESTED',
                RequestedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                RequestedBy NVARCHAR(256) NOT NULL,
                ReviewedAt DATETIME2 NULL,
                ReviewedBy NVARCHAR(256) NULL,
                AdminResponse NVARCHAR(2000) NULL,
                CONSTRAINT PK_GraphScoringChangeRequests PRIMARY KEY (RequestId),
                CONSTRAINT FK_GraphScoringChangeRequests_Tenant
                    FOREIGN KEY (TenantId) REFERENCES dbo.Tenants(TenantId),
                CONSTRAINT FK_GraphScoringChangeRequests_Policy
                    FOREIGN KEY (PolicyId) REFERENCES dbo.GraphScoringPolicies(PolicyId),
                CONSTRAINT FK_GraphScoringChangeRequests_ResolvedPolicy
                    FOREIGN KEY (ResolvedPolicyId) REFERENCES dbo.GraphScoringPolicies(PolicyId),
                CONSTRAINT CK_GraphScoringChangeRequests_Status CHECK (
                    Status IN ('REQUESTED','UNDER_REVIEW','APPROVED','REJECTED','PUBLISHED')
                ),
                CONSTRAINT CK_GraphScoringChangeRequests_SuggestedJson CHECK (
                    SuggestedParametersJson IS NULL OR ISJSON(SuggestedParametersJson)=1
                ),
                CONSTRAINT CK_GraphScoringChangeRequests_NominationsJson CHECK (
                    SupportingNominationIdsJson IS NULL OR
                    ISJSON(SupportingNominationIdsJson)=1
                )
            );
        """)
        op.execute("""
            CREATE INDEX IX_GraphScoringChangeRequests_TenantStatus
            ON dbo.GraphScoringChangeRequests(TenantId, Status, RequestedAt DESC);
        """)

    _seed_baseline_policies()


def downgrade() -> None:
    for table in (
        "GraphScoringChangeRequests",
        "GraphScoringPatternParameters",
        "GraphScoringPolicies",
    ):
        if _exists("table", table):
            op.execute(f"DROP TABLE dbo.{table};")

    for table, columns in (
        ("Graph_FraudScores", [
            "SnapshotRunId", "ScoringPolicyVersion", "ScoringStrategy",
            "WinningPatternType", "WinningFindingHash",
        ]),
        ("UserGraphFlags", ["FindingsJson"]),
        ("GraphPatternFindings", [
            "ScoreComponentsJson", "ScoringPolicyVersion", "FindingScore",
        ]),
    ):
        for column in columns:
            if _exists("column", column, table):
                op.execute(f"ALTER TABLE dbo.{table} DROP COLUMN {column};")
    op.execute("UPDATE dbo.Graph_FraudScores SET GraphScore=ROUND(GraphScore,0);")
    op.execute("UPDATE dbo.FraudDecisionResults SET GraphScore=ROUND(GraphScore,0);")
    _drop_graph_score_constraint()
    op.execute("ALTER TABLE dbo.Graph_FraudScores ALTER COLUMN GraphScore INT NOT NULL;")
    _create_graph_score_constraint()
    op.execute("ALTER TABLE dbo.FraudDecisionResults ALTER COLUMN GraphScore INT NULL;")
