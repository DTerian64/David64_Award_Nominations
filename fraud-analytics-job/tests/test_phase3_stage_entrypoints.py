"""Focused contracts for Phase 3 tenant-level stage entry points."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modeling import forecast_models
from utils.stage_result import TenantStageResult


class _ForecastCursor:
    def __init__(self, update_count: int):
        self.update_count = update_count
        self.rowcount = -1
        self.statements = []
        self.batch = None
        self.fast_executemany = False

    def execute(self, sql, *params):
        self.statements.append((sql, params))
        if "UPDATE dbo.ForecastRuns" in sql:
            self.rowcount = self.update_count
        return self

    def executemany(self, sql, rows):
        self.batch = (sql, rows)


class _ForecastConnection:
    def __init__(self, update_count: int):
        self.cursor_value = _ForecastCursor(update_count)
        self.committed = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True


def _persist(connection):
    forecast_models._persist(
        connection,
        "11111111-1111-1111-1111-111111111111",
        7,
        date(2026, 1, 1),
        date(2026, 9, 28),
        {"chosen": "ETS"},
        [
            (
                "nominations",
                "total",
                None,
                "weekly",
                date(2026, 10, 5),
                1,
                "ETS",
                10.0,
                8.0,
                12.0,
            )
        ],
    )


def test_forecast_retry_replaces_stable_run_atomically():
    connection = _ForecastConnection(update_count=1)

    _persist(connection)

    sql = "\n".join(statement for statement, _ in connection.cursor_value.statements)
    assert "DELETE FROM dbo.Forecasts WHERE RunId" in sql
    assert "UPDATE dbo.ForecastRuns" in sql
    assert "INSERT INTO dbo.ForecastRuns" not in sql
    assert connection.cursor_value.batch is not None
    assert connection.committed


def test_first_forecast_attempt_inserts_stable_run_header():
    connection = _ForecastConnection(update_count=0)

    _persist(connection)

    sql = "\n".join(statement for statement, _ in connection.cursor_value.statements)
    assert "INSERT INTO dbo.ForecastRuns" in sql


def test_stage_result_distinguishes_policy_skip_from_failure():
    skipped = TenantStageResult.skipped("BELOW_MINIMUM_VOLUME")
    succeeded = TenantStageResult.succeeded(published_version="model-v1")

    assert skipped.status == "SKIPPED"
    assert skipped.reason_code == "BELOW_MINIMUM_VOLUME"
    assert succeeded.status == "SUCCEEDED"
    assert succeeded.published_version == "model-v1"

