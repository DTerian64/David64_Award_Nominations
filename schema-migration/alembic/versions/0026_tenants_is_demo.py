"""Add is_demo flag to dbo.Tenants

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-17

Context
-------
Replaces the fragile pattern of identifying the demo tenant by TenantName
with an explicit boolean flag.  Previously sqlhelper2.py hardcoded the
string "Demo Awards" while seed_demo.py used "Terian Services Demo" —
a silent mismatch that caused get_demo_tenant_id() to return None.

With is_demo = 1 the demo tenant can be renamed freely without breaking
the self-registration flow.  An admin sets the flag directly in dbo.Tenants;
no code change is required.

Bootstrap
---------
The upgrade also fires a one-time UPDATE to flag the existing demo tenant
row (identified by name at migration time, the last time the name matters).

Downgrade
---------
Drops the column.  Existing demo SQL will need the name-based fallback
restored manually if rolling back.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEMO_TENANT_NAME = "Terian Services Demo"


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

    if not _column_exists(conn, "Tenants", "is_demo"):
        # SQL Server fills existing rows with 0 via the DEFAULT when NOT NULL is combined
        # with a DEFAULT constraint in a single ALTER TABLE statement.
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants ADD is_demo BIT NOT NULL DEFAULT 0"
        ))

    # One-time bootstrap: flag the existing demo tenant row.
    # This is the last time the tenant name is used to identify the demo tenant.
    conn.execute(
        sa.text("UPDATE dbo.Tenants SET is_demo = 1 WHERE TenantName = :name"),
        {"name": _DEMO_TENANT_NAME},
    )


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "Tenants", "is_demo"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants DROP COLUMN is_demo"
        ))
