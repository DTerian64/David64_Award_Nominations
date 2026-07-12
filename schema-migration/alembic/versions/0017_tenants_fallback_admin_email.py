"""Add fallback_admin_email to dbo.Tenants

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-31

Context
-------
When no HRBP users are configured for a tenant, the auxiliary service
falls back to emailing this address instead.  Kept as a first-class column
(not buried in the Config JSON blob) so it is easily queryable and auditable.

Downgrade
---------
Drops the column.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = :t AND COLUMN_NAME = :c AND TABLE_SCHEMA = 'dbo'"
        ),
        {"t": table_name, "c": column_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "Tenants", "fallback_admin_email"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants "
            "ADD fallback_admin_email NVARCHAR(256) NULL"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "Tenants", "fallback_admin_email"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants DROP COLUMN fallback_admin_email"
        ))
