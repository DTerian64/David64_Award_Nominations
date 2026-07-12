"""Convert text columns to NVARCHAR; rename DollarAmount → Amount; add Currency

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-21

Changes
-------
1. NVARCHAR conversion (user-facing free-text columns only):
     Users.FirstName            VARCHAR(128)  → NVARCHAR(128)
     Users.LastName             VARCHAR(128)  → NVARCHAR(128)
     Users.Title                VARCHAR(256)  → NVARCHAR(256)
     Nominations.NominationDescription  VARCHAR(500) → NVARCHAR(500)

   Internal / system columns (Status, flags, audit logs, UPNs) are left as
   VARCHAR because they only ever hold ASCII values.

2. Rename Nominations.DollarAmount → Amount
   The column was misleadingly named — not all tenants use dollars.

3. Add Nominations.Currency VARCHAR(3) NOT NULL
   Stores the ISO 4217 code at nomination time (USD, KRW, EUR …).
   Backfilled from Tenants.Config JSON for existing rows; defaults to 'USD'
   for any tenant whose Config is NULL (should not happen after migration 0002).

Downgrade
---------
Reverses all three changes (VARCHAR back, Amount → DollarAmount, drop Currency).
"""

from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects.mssql import NVARCHAR as MSSQL_NVARCHAR
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. NVARCHAR conversions (DDL — each ALTER COLUMN auto-commits) ───────
    nvarchar_alters = [
        ("Users",        "FirstName",             128),
        ("Users",        "LastName",              128),
        ("Users",        "Title",                 256),
        ("Nominations",  "NominationDescription", 500),
    ]
    for table, column, length in nvarchar_alters:
        # Check current type — skip if already NVARCHAR (idempotent re-run)
        row = conn.execute(sa.text(
            "SELECT DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=:t AND COLUMN_NAME=:c"
        ), {"t": table, "c": column}).fetchone()

        if row and row[0].lower() != "nvarchar":
            op.alter_column(
                table, column,
                existing_type=sa.String(length),
                type_=MSSQL_NVARCHAR(length),
                existing_nullable=True,
                schema="dbo",
            )

    # ── 2. Rename DollarAmount → Amount (DDL) ────────────────────────────────
    col_check = conn.execute(sa.text(
        "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='Nominations' "
        "AND COLUMN_NAME='DollarAmount'"
    )).fetchone()

    if col_check:
        op.execute(sa.text(
            "EXEC sp_rename 'dbo.Nominations.DollarAmount', 'Amount', 'COLUMN'"
        ))

    # ── 3. Add Currency column ────────────────────────────────────────────────
    currency_check = conn.execute(sa.text(
        "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='Nominations' "
        "AND COLUMN_NAME='Currency'"
    )).fetchone()

    if not currency_check:
        # Add as nullable first so existing rows don't violate NOT NULL
        op.add_column(
            "Nominations",
            sa.Column("Currency", sa.String(3), nullable=True),
            schema="dbo",
        )

        # Backfill from Tenants.Config JSON; fall back to 'USD'
        conn.execute(sa.text("""
            UPDATE n
            SET n.Currency = COALESCE(
                JSON_VALUE(t.Config, '$.currency'),
                'USD'
            )
            FROM dbo.Nominations n
            JOIN dbo.Users   u ON u.UserId   = n.NominatorId
            JOIN dbo.Tenants t ON t.TenantId = u.TenantId
        """))

        # Catch any rows whose nominator is missing — set to 'USD'
        conn.execute(sa.text(
            "UPDATE dbo.Nominations SET Currency = 'USD' WHERE Currency IS NULL"
        ))

        # Now tighten to NOT NULL
        op.alter_column(
            "Nominations", "Currency",
            existing_type=sa.String(3),
            nullable=False,
            schema="dbo",
        )

    conn.execute(sa.text("COMMIT"))


def downgrade() -> None:
    conn = op.get_bind()

    # Drop Currency
    op.drop_column("Nominations", "Currency", schema="dbo")

    # Amount → DollarAmount
    col_check = conn.execute(sa.text(
        "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='Nominations' "
        "AND COLUMN_NAME='Amount'"
    )).fetchone()
    if col_check:
        op.execute(sa.text(
            "EXEC sp_rename 'dbo.Nominations.Amount', 'DollarAmount', 'COLUMN'"
        ))

    # NVARCHAR → VARCHAR
    nvarchar_alters = [
        ("Users",        "FirstName",             128),
        ("Users",        "LastName",              128),
        ("Users",        "Title",                 256),
        ("Nominations",  "NominationDescription", 500),
    ]
    for table, column, length in nvarchar_alters:
        op.alter_column(
            table, column,
            existing_type=MSSQL_NVARCHAR(length),
            type_=sa.String(length),
            existing_nullable=True,
            schema="dbo",
        )

    conn.execute(sa.text("COMMIT"))
