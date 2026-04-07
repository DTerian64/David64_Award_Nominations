"""Add graph analytics tables for fraud pattern detection

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-07

Context
-------
Introduces the Azure SQL Graph layer and the GraphPatternFindings results table
that back the fraud-analytics-job weekly pipeline.

The pipeline runs two stages:
  1. train_fraud_model.py   — Random Forest retrain → dbo.FraudScores
  2. graph_pattern_detector.py — graph + NLP detection → dbo.GraphPatternFindings

Changes
-------

1. dbo.NomGraph_Person  (Azure SQL Graph NODE table)
   Projection of dbo.Users into graph node form. Synced from Users by the
   detector on every run (TRUNCATE + INSERT). Tenant-aware (TenantId column).

   Azure SQL automatically adds the hidden $node_id column; do not reference it
   in application queries — use UserId as the business key.

2. dbo.NomGraph_Nominated  (Azure SQL Graph EDGE table)
   Projection of dbo.Nominations into graph edge form. Synced from Nominations
   on every run. Edges point FROM nominator person TO beneficiary person.

   Azure SQL automatically adds $edge_id, $from_id, $to_id. The MATCH clause
   in T-SQL uses these implicitly:
       MATCH(nominator-(nomination)->beneficiary)

3. dbo.GraphPatternFindings  (regular results table)
   One row per detected pattern instance per weekly run. Written by the detector;
   read by the Integrity tab in the frontend.

   RunId groups all findings from a single execution, enabling the UI to
   compare runs over time ("last week vs this week").

   PatternType values (enforced by detector, not a DB constraint):
     Ring | SuperNominator | Desert | ApproverAffinity |
     CopyPaste | TransactionalLanguage | HiddenCandidate

   Severity values: Low | Medium | High | Critical

   AffectedUsers, NominationIds: JSON arrays stored as NVARCHAR(MAX).
   SQL Server 2016+ ISJSON() can validate them; frontend parses with JSON.parse().

Note on autogenerate
--------------------
Alembic cannot autogenerate AS NODE / AS EDGE DDL — those are SQL Server graph
extensions unknown to SQLAlchemy's type system. This migration is hand-written
and uses op.execute() for all three CREATE TABLE statements.
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Helpers (same pattern as earlier migrations) ──────────────────────────────

def _table_exists(conn, table_name: str) -> bool:
    return conn.execute(sa.text(
        "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :t"
    ), {"t": table_name}).fetchone() is not None


def _index_exists(conn, index_name: str, table_name: str) -> bool:
    return conn.execute(sa.text(
        "SELECT 1 FROM sys.indexes "
        "WHERE name = :i AND object_id = OBJECT_ID('dbo.' + :t)"
    ), {"i": index_name, "t": table_name}).fetchone() is not None


# ── Upgrade ───────────────────────────────────────────────────────────────────

def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. NomGraph_Person — Azure SQL Graph NODE table ───────────────────────
    # AS NODE signals SQL Server to treat this as a graph node table.
    # SQL Server adds a hidden $node_id column automatically.
    # UserId is the business key used in all application queries.
    if not _table_exists(conn, "NomGraph_Person"):
        conn.execute(sa.text("""
            CREATE TABLE dbo.NomGraph_Person (
                UserId   INT           NOT NULL,
                FullName NVARCHAR(100) NOT NULL,
                TenantId INT           NOT NULL
            ) AS NODE
        """))

        # Clustered index on UserId — the primary lookup key for MATCH queries.
        conn.execute(sa.text(
            "CREATE UNIQUE CLUSTERED INDEX ux_nomgraph_person_userid "
            "ON dbo.NomGraph_Person (UserId)"
        ))

        # Non-clustered index on TenantId — detector filters by tenant.
        conn.execute(sa.text(
            "CREATE INDEX ix_nomgraph_person_tenantid "
            "ON dbo.NomGraph_Person (TenantId)"
        ))

    # ── 2. NomGraph_Nominated — Azure SQL Graph EDGE table ────────────────────
    # AS EDGE signals SQL Server to add $edge_id, $from_id, $to_id columns.
    # Edges point FROM the nominator's $node_id TO the beneficiary's $node_id.
    # NominationId ties each edge back to the source Nominations row.
    # Status and Amount are carried on the edge for graph-native queries
    # (e.g. "find rings where all edges have Status = Paid").
    if not _table_exists(conn, "NomGraph_Nominated"):
        conn.execute(sa.text("""
            CREATE TABLE dbo.NomGraph_Nominated (
                NominationId INT          NOT NULL,
                Amount       INT          NULL,
                Status       NVARCHAR(20) NULL,
                NomDate      DATE         NULL
            ) AS EDGE
        """))

        # Index on NominationId — supports edge → Nominations FK lookups.
        conn.execute(sa.text(
            "CREATE INDEX ix_nomgraph_nominated_nominationid "
            "ON dbo.NomGraph_Nominated (NominationId)"
        ))

    # ── 3. GraphPatternFindings — results table ───────────────────────────────
    # One row per detected pattern instance per weekly run.
    # RunId (UNIQUEIDENTIFIER) groups all findings from a single execution,
    # enabling the Integrity tab to show the latest run's findings and
    # compare against previous runs.
    if not _table_exists(conn, "GraphPatternFindings"):
        op.create_table(
            "GraphPatternFindings",
            sa.Column("FindingId",     sa.Integer(),      nullable=False,
                      autoincrement=True),
            sa.Column("TenantId",      sa.Integer(),      nullable=False),
            sa.Column("PatternType",   sa.String(50),     nullable=False),
            sa.Column("Severity",      sa.String(20),     nullable=False),
            sa.Column("AffectedUsers", sa.Text(),         nullable=True),   # JSON array
            sa.Column("NominationIds", sa.Text(),         nullable=True),   # JSON array
            sa.Column("Detail",        sa.String(1000),   nullable=True),
            sa.Column("DetectedAt",    sa.DateTime(),     nullable=False,
                      server_default=sa.text("GETDATE()")),
            sa.Column("RunId",         sa.String(36),     nullable=False),  # GUID string
            sa.PrimaryKeyConstraint("FindingId", name="PK_GraphPatternFindings"),
            schema="dbo",
        )

        # Most common query: "latest run's findings for tenant X, grouped by type"
        conn.execute(sa.text(
            "CREATE INDEX ix_graphpatternfindings_tenant_pattern "
            "ON dbo.GraphPatternFindings (TenantId, PatternType, DetectedAt DESC)"
        ))

        # Supports "show all findings for this run" (Integrity tab detail view)
        conn.execute(sa.text(
            "CREATE INDEX ix_graphpatternfindings_runid "
            "ON dbo.GraphPatternFindings (RunId)"
        ))

    conn.execute(sa.text("COMMIT"))


# ── Downgrade ─────────────────────────────────────────────────────────────────

def downgrade() -> None:
    conn = op.get_bind()

    if _table_exists(conn, "GraphPatternFindings"):
        conn.execute(sa.text(
            "DROP INDEX IF EXISTS ix_graphpatternfindings_runid "
            "ON dbo.GraphPatternFindings"
        ))
        conn.execute(sa.text(
            "DROP INDEX IF EXISTS ix_graphpatternfindings_tenant_pattern "
            "ON dbo.GraphPatternFindings"
        ))
        op.drop_table("GraphPatternFindings", schema="dbo")

    if _table_exists(conn, "NomGraph_Nominated"):
        conn.execute(sa.text(
            "DROP INDEX IF EXISTS ix_nomgraph_nominated_nominationid "
            "ON dbo.NomGraph_Nominated"
        ))
        conn.execute(sa.text("DROP TABLE dbo.NomGraph_Nominated"))

    if _table_exists(conn, "NomGraph_Person"):
        conn.execute(sa.text(
            "DROP INDEX IF EXISTS ix_nomgraph_person_tenantid "
            "ON dbo.NomGraph_Person"
        ))
        conn.execute(sa.text(
            "DROP INDEX IF EXISTS ux_nomgraph_person_userid "
            "ON dbo.NomGraph_Person"
        ))
        conn.execute(sa.text("DROP TABLE dbo.NomGraph_Person"))
