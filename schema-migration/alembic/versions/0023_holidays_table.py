"""Country-keyed public-holidays table for forecasting seasonality

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-15

Context
-------
The forecast models use an ``is_holiday`` calendar feature so the bake-off can
learn the dips around public holidays. Holidays were previously a hardcoded US
set in modeling/forecast_models.py — this table replaces that with per-country data so
the multi-country rollout works for any tenant.

Keyed by **country** (not tenant): holidays are national, so every US tenant
shares the 'US' rows. A tenant's country is derived from its Config.locale
region (e.g. 'en-US' → 'US', 'ko-KR' → 'KR'). The weekly job's
misc_jobs/sync_holidays.py stage refreshes this table from the internet
(Nager.Date) with an offline
fallback; the forecast models only ever read from here, so a failed sync can
never break a run.

Downgrade drops the table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    return conn.execute(sa.text(
        "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :t"
    ), {"t": table_name}).fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "Holidays"):
        op.create_table(
            "Holidays",
            sa.Column("CountryCode", sa.String(2),    nullable=False),   # ISO 3166-1 alpha-2
            sa.Column("HolidayDate", sa.Date(),       nullable=False),
            sa.Column("Name",        sa.Unicode(200), nullable=True),
            sa.Column("Source",      sa.String(50),   nullable=True),    # 'nager.date' | 'holidays-lib'
            sa.Column("UpdatedAt",   sa.DateTime(),   nullable=False,
                      server_default=sa.text("GETUTCDATE()")),
            sa.PrimaryKeyConstraint("CountryCode", "HolidayDate", name="PK_Holidays"),
            schema="dbo",
        )
        # PK already indexes (CountryCode, HolidayDate); add a date index for
        # "all holidays in year Y" style lookups during sync.
        conn.execute(sa.text(
            "CREATE INDEX ix_holidays_date ON dbo.Holidays (HolidayDate)"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "Holidays"):
        conn.execute(sa.text("DROP INDEX IF EXISTS ix_holidays_date ON dbo.Holidays"))
        op.drop_table("Holidays", schema="dbo")
