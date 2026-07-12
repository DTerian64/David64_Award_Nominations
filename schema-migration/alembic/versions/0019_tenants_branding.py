"""Add Company_Logo_URL and Tagline to dbo.Tenants

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-02

Context
-------
Adds two branding columns to dbo.Tenants used by the public
GET /api/tenant/branding endpoint to personalise the pre-login screen:

  Company_Logo_URL  — absolute URL of the tenant's logo image (PNG/SVG).
              Displayed on the login splash above the company name.
              NULL → no logo shown.

  Tagline   — short welcome string, e.g. "Employee Recognition Portal".
              NULL → frontend falls back to a generic default.

Both columns are nullable; existing tenants simply show no logo / default
tagline until populated (e.g. via Azure Portal or a SQL script).

Downgrade
---------
Drops both columns.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
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
    if not _column_exists(conn, "Tenants", "Company_Logo_URL"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants ADD Company_Logo_URL NVARCHAR(500) NULL"
        ))
    if not _column_exists(conn, "Tenants", "Tagline"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants ADD Tagline NVARCHAR(200) NULL"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "Tenants", "Tagline"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants DROP COLUMN Tagline"
        ))
    if _column_exists(conn, "Tenants", "Company_Logo_URL"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants DROP COLUMN Company_Logo_URL"
        ))
