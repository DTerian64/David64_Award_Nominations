"""Remove the six superseded integrity score tables.

Revision ID: 0053
Revises: 0052
Create Date: 2026-09-02

IntegrityDecisionResults is the sole nomination-time integrity decision and
human-adjudication record. The removed RF/GNN tables are not replaced: batch
score comparison by model version was deliberately retired with this revision.

The migration refuses to discard a legacy row unless its nomination already
has a canonical decision. Revision 0048 performed the historical backfill; this
guard also catches partial writes created between that backfill and this cutover.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

_LEGACY_TABLES = (
    "HRBP_FraudFlags",
    "Graph_FraudScores",
    "GNN_FraudScores",
    "Appr_FraudScores",
    "P2P_FraudScores",
    "FraudDecisionResults",
)


def _table_exists(name: str) -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :name"
        ),
        {"name": name},
    ).fetchone() is not None


def _unmapped_count(name: str) -> int:
    # Table names come only from the module constant above, never user input.
    return int(op.get_bind().execute(sa.text(f"""
        SELECT COUNT_BIG(*)
        FROM dbo.{name} legacy
        WHERE NOT EXISTS (
            SELECT 1
            FROM dbo.IntegrityDecisionResults canonical
            WHERE canonical.NominationId = legacy.NominationId
        );
    """)).scalar_one())


def _database_dependents(name: str) -> list[str]:
    rows = op.get_bind().execute(
        sa.text("""
            SELECT DISTINCT
                OBJECT_SCHEMA_NAME(referencing_id) + '.' +
                OBJECT_NAME(referencing_id)
            FROM sys.sql_expression_dependencies
            WHERE referenced_id = OBJECT_ID(:qualified_name)
              AND referencing_id <> referenced_id;
        """),
        {"qualified_name": f"dbo.{name}"},
    ).fetchall()
    return sorted(str(row[0]) for row in rows if row[0])


def upgrade() -> None:
    if not _table_exists("IntegrityDecisionResults"):
        raise RuntimeError(
            "0053 requires dbo.IntegrityDecisionResults before legacy tables "
            "can be removed"
        )

    for table in _LEGACY_TABLES:
        if not _table_exists(table):
            continue
        unmapped = _unmapped_count(table)
        if unmapped:
            raise RuntimeError(
                f"0053 cannot drop dbo.{table}: {unmapped} rows have no "
                "dbo.IntegrityDecisionResults record"
            )
        dependents = _database_dependents(table)
        if dependents:
            raise RuntimeError(
                f"0053 cannot drop dbo.{table}: database objects still depend "
                f"on it: {', '.join(dependents)}"
            )

    for table in _LEGACY_TABLES:
        if _table_exists(table):
            op.drop_table(table, schema="dbo")


def downgrade() -> None:
    raise RuntimeError(
        "0053 is intentionally irreversible: recreating empty legacy tables "
        "would not restore their discarded score history"
    )
