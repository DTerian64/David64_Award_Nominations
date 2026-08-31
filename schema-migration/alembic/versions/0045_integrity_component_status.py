"""Persist tenant component availability and nomination-time reasons.

Revision ID: 0045
Revises: 0044
Create Date: 2026-08-24

``IntegrityComponentStatus`` is the operational source of truth for RF, Graph,
and GNN availability.  Serving state and last-attempt state are deliberately
separate: a failed or skipped rebuild must not hide an older usable artifact.

The table is system-versioned so producer status transitions remain auditable.
``FraudDecisionResults`` snapshots the unavailable reason seen when an
individual nomination was scored.
"""

import sqlalchemy as sa
from alembic import op


revision = "0045"
down_revision = "0044"
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
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :table "
            "AND COLUMN_NAME = :column"
        ),
        {"table": table, "column": column},
    ).fetchone() is not None


_DECISION_COLUMNS = (
    ("RfUnavailableReasonCode", "VARCHAR(64) NULL"),
    ("RfUnavailableReasonDetail", "NVARCHAR(1000) NULL"),
    ("GraphUnavailableReasonCode", "VARCHAR(64) NULL"),
    ("GraphUnavailableReasonDetail", "NVARCHAR(1000) NULL"),
    ("GnnUnavailableReasonCode", "VARCHAR(64) NULL"),
    ("GnnUnavailableReasonDetail", "NVARCHAR(1000) NULL"),
)


def upgrade() -> None:
    if not _table_exists("IntegrityComponentStatus"):
        op.execute("""
            CREATE TABLE dbo.IntegrityComponentStatus (
                TenantId             INT NOT NULL,
                Component            VARCHAR(10) NOT NULL,
                ServingStatus        VARCHAR(20) NOT NULL,
                ServingVersion       VARCHAR(64) NULL,
                ServingAsOf          DATETIME2 NULL,
                LastAttemptStatus    VARCHAR(20) NOT NULL,
                ReasonCode           VARCHAR(64) NULL,
                ReasonDetail         NVARCHAR(1000) NULL,
                DiagnosticsJson      NVARCHAR(MAX) NULL,
                LastAttemptAt        DATETIME2 NOT NULL,
                LastSuccessfulAt     DATETIME2 NULL,
                RunId                VARCHAR(64) NULL,
                CreatedAt            DATETIME2 NOT NULL
                    CONSTRAINT DF_IntegrityComponentStatus_CreatedAt
                    DEFAULT SYSUTCDATETIME(),
                CreatedBy            NVARCHAR(256) NOT NULL,
                UpdatedAt            DATETIME2 NOT NULL
                    CONSTRAINT DF_IntegrityComponentStatus_UpdatedAt
                    DEFAULT SYSUTCDATETIME(),
                UpdatedBy            NVARCHAR(256) NOT NULL,
                ValidFrom            DATETIME2(3) GENERATED ALWAYS AS ROW START HIDDEN
                    CONSTRAINT DF_IntegrityComponentStatus_ValidFrom
                    DEFAULT SYSUTCDATETIME(),
                ValidTo              DATETIME2(3) GENERATED ALWAYS AS ROW END HIDDEN
                    CONSTRAINT DF_IntegrityComponentStatus_ValidTo
                    DEFAULT CONVERT(DATETIME2(3), '9999-12-31 23:59:59.999'),
                PERIOD FOR SYSTEM_TIME (ValidFrom, ValidTo),
                CONSTRAINT PK_IntegrityComponentStatus
                    PRIMARY KEY CLUSTERED (TenantId, Component),
                CONSTRAINT FK_IntegrityComponentStatus_Tenants
                    FOREIGN KEY (TenantId) REFERENCES dbo.Tenants (TenantId),
                CONSTRAINT CK_IntegrityComponentStatus_Component
                    CHECK (Component IN ('RF','GRAPH','GNN')),
                CONSTRAINT CK_IntegrityComponentStatus_ServingStatus
                    CHECK (ServingStatus IN ('UNKNOWN','AVAILABLE','UNAVAILABLE','STALE')),
                CONSTRAINT CK_IntegrityComponentStatus_AttemptStatus
                    CHECK (LastAttemptStatus IN ('SUCCEEDED','SKIPPED','FAILED','DISABLED')),
                CONSTRAINT CK_IntegrityComponentStatus_DiagnosticsJson
                    CHECK (DiagnosticsJson IS NULL OR ISJSON(DiagnosticsJson) = 1)
            ) WITH (
                SYSTEM_VERSIONING = ON (
                    HISTORY_TABLE = dbo.IntegrityComponentStatus_History
                )
            );
        """)
        op.execute("""
            CREATE INDEX IX_IntegrityComponentStatus_Component
                ON dbo.IntegrityComponentStatus (Component, ServingStatus, LastAttemptAt DESC);
        """)

    if _table_exists("FraudDecisionResults"):
        for column, sql_type in _DECISION_COLUMNS:
            if not _column_exists("FraudDecisionResults", column):
                op.execute(
                    f"ALTER TABLE dbo.FraudDecisionResults ADD {column} {sql_type};"
                )


def downgrade() -> None:
    if _table_exists("FraudDecisionResults"):
        for column, _ in reversed(_DECISION_COLUMNS):
            if _column_exists("FraudDecisionResults", column):
                op.execute(f"ALTER TABLE dbo.FraudDecisionResults DROP COLUMN {column};")

    if _table_exists("IntegrityComponentStatus"):
        op.execute(
            "ALTER TABLE dbo.IntegrityComponentStatus SET (SYSTEM_VERSIONING = OFF);"
        )
        op.execute("DROP TABLE dbo.IntegrityComponentStatus;")
        if _table_exists("IntegrityComponentStatus_History"):
            op.execute("DROP TABLE dbo.IntegrityComponentStatus_History;")
