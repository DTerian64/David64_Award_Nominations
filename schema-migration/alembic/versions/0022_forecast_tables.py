"""Forecasting result tables: ForecastRuns + Forecasts

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-14

Context
-------
Backs the predictive Forecasting feature. The weekly batch job
(forecast_models stage) runs a model bake-off — Seasonal-Naive vs ETS
(Holt-Winters) vs LightGBM — with a rolling-origin backtest, then writes the
chosen forecast per series here. The analytics API reads the latest run instead
of computing live (the lightweight on-demand Holt remains a fallback).

Mirrors the integrity runs/findings pattern (see 0006): a RunId groups all rows
produced by a single job execution, so the UI can show the latest run and the
per-model comparison that produced it.

Tables
------
dbo.ForecastRuns
    One row per job execution per tenant. Metrics holds the full per-series,
    per-model backtest comparison as JSON (MASE/sMAPE/RMSE/coverage + the model
    chosen for each series).

dbo.Forecasts
    One row per (run, series, level, department, target period). Series is
    'nominations' or 'spend'; Level is 'total' or 'department' (DepartmentTitle
    set only for department rows); Grain is 'weekly' or 'daily'.

Downgrade drops both tables.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── Helpers (same pattern as earlier migrations) ──────────────────────────────

def _table_exists(conn, table_name: str) -> bool:
    return conn.execute(sa.text(
        "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :t"
    ), {"t": table_name}).fetchone() is not None


# ── Upgrade ───────────────────────────────────────────────────────────────────

def upgrade() -> None:
    conn = op.get_bind()

    # ── ForecastRuns — one row per job execution per tenant ───────────────────
    if not _table_exists(conn, "ForecastRuns"):
        op.create_table(
            "ForecastRuns",
            sa.Column("RunId",         sa.String(36),   nullable=False),   # GUID string
            sa.Column("TenantId",      sa.Integer(),    nullable=False),
            sa.Column("GeneratedAt",   sa.DateTime(),   nullable=False,
                      server_default=sa.text("GETDATE()")),
            sa.Column("HorizonWeeks",  sa.Integer(),    nullable=False),
            sa.Column("HistoryStart",  sa.Date(),       nullable=True),
            sa.Column("HistoryEnd",    sa.Date(),       nullable=True),
            sa.Column("Confidence",    sa.Float(),      nullable=False,
                      server_default=sa.text("0.8")),
            # JSON: { "<series>": { "<model>": {MASE,sMAPE,RMSE,coverage,folds}, ...,
            #                       "chosen": "<model>" }, ... }
            sa.Column("Metrics",       sa.Text(),       nullable=True),
            sa.Column("Status",        sa.String(20),   nullable=False,
                      server_default="complete"),
            sa.PrimaryKeyConstraint("RunId", name="PK_ForecastRuns"),
            schema="dbo",
        )
        conn.execute(sa.text(
            "CREATE INDEX ix_forecastruns_tenant_generated "
            "ON dbo.ForecastRuns (TenantId, GeneratedAt DESC)"
        ))

    # ── Forecasts — one row per (run, series, level, dept, target period) ─────
    if not _table_exists(conn, "Forecasts"):
        op.create_table(
            "Forecasts",
            sa.Column("ForecastId",      sa.Integer(),    nullable=False, autoincrement=True),
            sa.Column("RunId",           sa.String(36),   nullable=False),
            sa.Column("TenantId",        sa.Integer(),    nullable=False),
            sa.Column("Series",          sa.String(20),   nullable=False),   # nominations | spend
            sa.Column("Level",           sa.String(20),   nullable=False),   # total | department
            sa.Column("DepartmentTitle", sa.Unicode(100), nullable=True),
            sa.Column("Grain",           sa.String(10),   nullable=False),   # weekly | daily
            sa.Column("TargetDate",      sa.Date(),       nullable=False),
            sa.Column("Horizon",         sa.Integer(),    nullable=False),   # step 1..H
            sa.Column("Model",           sa.String(30),   nullable=False),
            sa.Column("PointForecast",   sa.Float(),      nullable=False),
            sa.Column("Lower",           sa.Float(),      nullable=True),
            sa.Column("Upper",           sa.Float(),      nullable=True),
            sa.PrimaryKeyConstraint("ForecastId", name="PK_Forecasts"),
            schema="dbo",
        )
        # Primary read: "latest run's forecast for tenant X, series S, level L"
        conn.execute(sa.text(
            "CREATE INDEX ix_forecasts_tenant_series_run "
            "ON dbo.Forecasts (TenantId, Series, Level, RunId)"
        ))
        conn.execute(sa.text(
            "CREATE INDEX ix_forecasts_runid ON dbo.Forecasts (RunId)"
        ))


# ── Downgrade ─────────────────────────────────────────────────────────────────

def downgrade() -> None:
    conn = op.get_bind()

    if _table_exists(conn, "Forecasts"):
        conn.execute(sa.text("DROP INDEX IF EXISTS ix_forecasts_runid ON dbo.Forecasts"))
        conn.execute(sa.text("DROP INDEX IF EXISTS ix_forecasts_tenant_series_run ON dbo.Forecasts"))
        op.drop_table("Forecasts", schema="dbo")

    if _table_exists(conn, "ForecastRuns"):
        conn.execute(sa.text("DROP INDEX IF EXISTS ix_forecastruns_tenant_generated ON dbo.ForecastRuns"))
        op.drop_table("ForecastRuns", schema="dbo")
