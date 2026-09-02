"""Remove transactional-language state from Graph Analytics.

TransactionalPhraseScore is now computed directly from nomination text by the
Random Forest at both training and inference. Graph snapshots are invalidated
so no embedded legacy finding can survive the synthetic-data reset.

Revision ID: 0051
Revises: 0050
Create Date: 2026-09-02
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    return op.get_bind().execute(sa.text("""
        SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=:table AND COLUMN_NAME=:column
    """), {"table": table, "column": column}).fetchone() is not None


def upgrade() -> None:
    op.execute("""
        DELETE FROM dbo.GraphScoringPatternParameters
        WHERE PatternType='TransactionalLanguage';
    """)
    op.execute("""
        DELETE FROM dbo.GraphPatternFindings
        WHERE PatternType='TransactionalLanguage';
    """)

    # FindingsJson denormalizes complete findings, so rebuilding the snapshot is
    # safer than trying to edit JSON in place. IntegrityComponentStatus prevents
    # the inference worker from treating the missing snapshot as a clean result.
    op.execute("DELETE FROM dbo.UserGraphFlags;")
    op.execute("""
        UPDATE dbo.IntegrityComponentStatus
        SET ServingStatus='UNAVAILABLE',
            ReasonCode='GRAPH_REFRESH_REQUIRED',
            ReasonDetail='Graph detector ownership changed; run Graph Analytics.',
            UpdatedAt=SYSUTCDATETIME(),
            UpdatedBy='migration:0051'
        WHERE Component='GRAPH';
    """)

    if _column_exists("UserGraphFlags", "HasTransactionalLanguage"):
        op.execute("""
            DECLARE @constraint_name sysname;
            SELECT @constraint_name=dc.name
            FROM sys.default_constraints dc
            JOIN sys.columns c
              ON c.object_id=dc.parent_object_id
             AND c.column_id=dc.parent_column_id
            JOIN sys.tables t ON t.object_id=c.object_id
            JOIN sys.schemas s ON s.schema_id=t.schema_id
            WHERE s.name='dbo' AND t.name='UserGraphFlags'
              AND c.name='HasTransactionalLanguage';
            IF @constraint_name IS NOT NULL
                EXEC('ALTER TABLE dbo.UserGraphFlags DROP CONSTRAINT ['
                     + @constraint_name + ']');
            ALTER TABLE dbo.UserGraphFlags DROP COLUMN HasTransactionalLanguage;
        """)


def downgrade() -> None:
    if not _column_exists("UserGraphFlags", "HasTransactionalLanguage"):
        op.execute("""
            ALTER TABLE dbo.UserGraphFlags
            ADD HasTransactionalLanguage BIT NOT NULL
                CONSTRAINT DF_UserGraphFlags_HasTransactionalLanguage DEFAULT 0;
        """)

    parameters = json.dumps({
        "minimum_hits": 2,
        "hit_reference": 6,
        "hit_weight": 45,
        "exposure_weight": 15,
        "amount_reference": 5000,
    }, separators=(",", ":"))
    op.get_bind().execute(sa.text("""
        INSERT INTO dbo.GraphScoringPatternParameters (
            PolicyId, PatternType, DisplayOrder, Enabled, EnabledForRouting,
            ApplicableRolesJson, BaseScore, MinimumScore, MaximumScore,
            ParametersJson, CreatedBy, UpdatedBy
        )
        SELECT PolicyId, 'TransactionalLanguage', 6, 1, 1,
               '["nominator"]', 40, 0, 100,
               :parameters, 'migration:0051-downgrade', 'migration:0051-downgrade'
        FROM dbo.GraphScoringPolicies;
    """), {"parameters": parameters})
