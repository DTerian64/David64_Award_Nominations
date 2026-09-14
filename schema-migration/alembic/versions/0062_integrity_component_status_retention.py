"""Limit online IntegrityComponentStatus temporal history to 24 months.

Revision ID: 0062
Revises: 0061

The table is an operational model registry, not the immutable nomination
decision record. Two years retains roughly 104 weekly attempts per component
and tenant while preventing DiagnosticsJson history from growing indefinitely.
"""

import sqlalchemy as sa
from alembic import op


revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def _is_temporal() -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT temporal_type FROM sys.tables "
            "WHERE object_id = OBJECT_ID('dbo.IntegrityComponentStatus')"
        )
    ).scalar() == 2


def upgrade() -> None:
    if not _is_temporal():
        raise RuntimeError(
            "IntegrityComponentStatus must remain system-versioned before "
            "its history retention policy can be configured"
        )
    op.execute("""
        ALTER TABLE dbo.IntegrityComponentStatus
        SET (
            SYSTEM_VERSIONING = ON (
                HISTORY_RETENTION_PERIOD = 24 MONTHS
            )
        );
    """)


def downgrade() -> None:
    if not _is_temporal():
        raise RuntimeError(
            "IntegrityComponentStatus is no longer system-versioned"
        )
    op.execute("""
        ALTER TABLE dbo.IntegrityComponentStatus
        SET (
            SYSTEM_VERSIONING = ON (
                HISTORY_RETENTION_PERIOD = INFINITE
            )
        );
    """)
