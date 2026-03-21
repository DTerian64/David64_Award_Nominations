"""Add Config JSON column to Tenants table and seed ACME Corp config

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-20

Context
-------
Adds a nullable NVARCHAR(MAX) column ``Config`` to the ``Tenants`` table.
The column stores a JSON object with per-tenant UI customisation settings:

  {
    "locale":   "ko-KR",          // BCP 47 locale tag (drives i18n)
    "currency": "KRW",            // ISO 4217 currency code
    "theme": {
      "primaryColor":       "#0d9488",   // Tailwind teal-600
      "primaryHoverColor":  "#0f766e",   // teal-700
      "primaryLightColor":  "#ccfbf1",   // teal-100
      "primaryTextOnDark":  "#ffffff"
    }
  }

NULL means "use application defaults" (en-US, USD, indigo theme).

Upgrade
-------
1. Add Config column (nullable, no default)
2. Seed ACME Corp (TenantId = 2) with Korean / teal config

Downgrade
---------
Remove the Config column.
"""

from typing import Sequence, Union
import json

import sqlalchemy as sa
from sqlalchemy.dialects.mssql import NVARCHAR as MSSQL_NVARCHAR
from alembic import op

# revision identifiers
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ── ACME Corp config ────────────────────────────────────────────────────────
_ACME_CONFIG = {
    "locale":   "ko-KR",
    "currency": "KRW",
    "theme": {
        "primaryColor":      "#0d9488",   # teal-600
        "primaryHoverColor": "#0f766e",   # teal-700
        "primaryLightColor": "#ccfbf1",   # teal-100
        "primaryTextOnDark": "#ffffff",
    },
}


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Add Config column if it doesn't already exist ────────────────────
    col_exists = conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'Tenants' "
            "AND COLUMN_NAME = 'Config'"
        )
    ).fetchone()

    if not col_exists:
        op.add_column(
            "Tenants",
            # NVARCHAR(MAX) — not UnicodeText which maps to deprecated NTEXT
            sa.Column("Config", MSSQL_NVARCHAR(None), nullable=True),
            schema="dbo",
        )

    # ── 2. Seed ACME Corp (TenantId = 2) with Korean / teal config ──────────
    conn.execute(
        sa.text(
            "UPDATE dbo.Tenants SET Config = :cfg WHERE TenantId = 2"
        ),
        {"cfg": json.dumps(_ACME_CONFIG, ensure_ascii=False)},
    )


def downgrade() -> None:
    op.drop_column("Tenants", "Config", schema="dbo")
