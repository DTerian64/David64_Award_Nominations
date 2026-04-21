"""Add Config JSON column to Tenants table and seed all tenant configs

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

Every tenant row is explicitly seeded so that NULL in Config always means
"row not yet migrated" rather than "use defaults".  The backend logs a
warning if it encounters a NULL config and falls back to the application
defaults (en-US / USD / indigo).

Upgrade
-------
1. Add Config column (nullable, no default)
2. Seed Tenant 1 (David64 / default org) with en-US / USD / indigo config
3. Seed Tenant 2 (ACME Corp) with ko-KR / KRW / teal config

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

# ── Per-tenant config seeds ──────────────────────────────────────────────────

# Tenant 1 — default org (en-US, USD, indigo theme)
_TENANT1_CONFIG = {
    "locale":   "en-US",
    "currency": "USD",
    "theme": {
        "primaryColor":      "#4f46e5",   # indigo-600
        "primaryHoverColor": "#4338ca",   # indigo-700
        "primaryLightColor": "#e0e7ff",   # indigo-100
        "primaryTextOnDark": "#ffffff",
    },
}

# Tenant 2 — ACME Corp (ko-KR, KRW, teal theme)
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

    # ── 1. Add Config column (DDL — implicitly commits on SQL Server) ────────
    # This MUST be done first and separately from the DML seeds below, because
    # SQL Server's DDL auto-commits the current transaction.  Mixing DDL and
    # DML in one transaction on SQL Server silently drops the DML if the DDL
    # triggers an implicit commit mid-flight.
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

    # ── 2. Seed all tenants (DML — runs after DDL has committed) ────────────
    # Use ISNULL guard so a re-run never overwrites a manually-set config.
    seeds = [
        (1, json.dumps(_TENANT1_CONFIG, ensure_ascii=False)),
        (2, json.dumps(_ACME_CONFIG,    ensure_ascii=False)),
    ]
    for tenant_id, cfg in seeds:
        result = conn.execute(
            sa.text(
                "UPDATE dbo.Tenants SET Config = :cfg "
                "WHERE TenantId = :tid AND Config IS NULL"
            ),
            {"cfg": cfg, "tid": tenant_id},
        )
        rows = result.rowcount
        if rows == 0:
            # Either TenantId doesn't exist or Config was already set — log it
            existing = conn.execute(
                sa.text("SELECT Config FROM dbo.Tenants WHERE TenantId = :tid"),
                {"tid": tenant_id},
            ).fetchone()
            if existing is None:
                raise RuntimeError(
                    f"Tenant seed failed: no row with TenantId={tenant_id} found in dbo.Tenants. "
                    "Ensure the tenant exists before running this migration."
                )
            # Config already populated — skip (idempotent re-run)
    conn.execute(sa.text("COMMIT"))


def downgrade() -> None:
    op.drop_column("Tenants", "Config", schema="dbo")
