"""Make GNN embedding snapshots independently version-addressable.

Revision ID: 0058
Revises: 0057

The original key allowed only one embedding per user and calendar day. An
operational candidate rerun on the same day could therefore overwrite the
incumbent version before the serving pointer moved. Adding ModelVersion makes
the embedding update and IntegrityComponentStatus pointer change safely
committable as one transaction while retaining the incumbent for rollback.
"""

import sqlalchemy as sa
from alembic import op


revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def _table_exists(table: str) -> bool:
    return bool(op.get_bind().execute(sa.text("""
        SELECT 1 FROM sys.tables
        WHERE object_id = OBJECT_ID(:table)
    """), {"table": f"dbo.{table}"}).scalar())


def _primary_key_columns(table: str) -> list[str]:
    rows = op.get_bind().execute(sa.text("""
        SELECT c.name
        FROM sys.key_constraints kc
        JOIN sys.index_columns ic
          ON ic.object_id = kc.parent_object_id
         AND ic.index_id = kc.unique_index_id
        JOIN sys.columns c
          ON c.object_id = ic.object_id
         AND c.column_id = ic.column_id
        WHERE kc.parent_object_id = OBJECT_ID(:table)
          AND kc.type = 'PK'
        ORDER BY ic.key_ordinal
    """), {"table": f"dbo.{table}"}).fetchall()
    return [str(row[0]) for row in rows]


def upgrade():
    if not _table_exists("GNN_UserEmbeddings"):
        return
    wanted = ["TenantId", "UserId", "ModelVersion", "AsOfDate"]
    if _primary_key_columns("GNN_UserEmbeddings") == wanted:
        return
    op.execute("""
        ALTER TABLE dbo.GNN_UserEmbeddings
        DROP CONSTRAINT PK_GNN_UserEmbeddings
    """)
    op.execute("""
        ALTER TABLE dbo.GNN_UserEmbeddings
        ADD CONSTRAINT PK_GNN_UserEmbeddings
        PRIMARY KEY CLUSTERED (TenantId, UserId, ModelVersion, AsOfDate)
    """)


def downgrade():
    if not _table_exists("GNN_UserEmbeddings"):
        return
    wanted = ["TenantId", "UserId", "AsOfDate"]
    if _primary_key_columns("GNN_UserEmbeddings") == wanted:
        return
    # A same-day multi-version history cannot fit the old key. Retain the most
    # recently written version for each old-key identity during downgrade.
    op.execute("""
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY TenantId, UserId, AsOfDate
                ORDER BY LastUpdatedUtc DESC, ModelVersion DESC
            ) AS version_rank
            FROM dbo.GNN_UserEmbeddings
        )
        DELETE FROM ranked WHERE version_rank > 1
    """)
    op.execute("""
        ALTER TABLE dbo.GNN_UserEmbeddings
        DROP CONSTRAINT PK_GNN_UserEmbeddings
    """)
    op.execute("""
        ALTER TABLE dbo.GNN_UserEmbeddings
        ADD CONSTRAINT PK_GNN_UserEmbeddings
        PRIMARY KEY CLUSTERED (TenantId, UserId, AsOfDate)
    """)
