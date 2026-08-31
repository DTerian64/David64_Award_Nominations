"""Add Site_URL to dbo.Tenants

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-31

Context
-------
Stores the tenant's frontend portal URL (e.g. https://awards.terianix.ai).
Used by the auxiliary service to embed a correct per-tenant hyperlink in
outbound emails (e.g. the HRBP review request email).

Kept separate from Domain (which is the canonical public hostname used for
domain-isolation checks) so the two concerns don't get conflated.

Downgrade
---------
Drops the column.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
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
    if not _column_exists(conn, "Tenants", "Site_URL"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants ADD Site_URL NVARCHAR(256) NULL"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "Tenants", "Site_URL"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants DROP COLUMN Site_URL"
        ))
