"""Tests for ProcessedEvents claim/retry semantics.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\integrity-check"
    python -m pytest tests/test_idempotency_claim.py -v
"""

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pyodbc
import pytest


os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")

from utils import db


class _Cursor:
    def __init__(self, existing):
        self.existing = existing
        self.calls = []
        self.rowcount = 0

    def execute(self, sql, params):
        self.calls.append((sql, params))
        if "INSERT INTO dbo.ProcessedEvents" in sql:
            raise pyodbc.IntegrityError("duplicate")
        if "UPDATE dbo.ProcessedEvents" in sql:
            self.rowcount = 1
        return self

    def fetchone(self):
        return self.existing


class _Connection:
    def __init__(self, existing):
        self.cursor_value = _Cursor(existing)
        self.commits = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1


@contextmanager
def _connection_context(connection):
    yield connection


def _claim(existing, now):
    connection = _Connection(existing)
    with patch("utils.db._get_conn", return_value=_connection_context(connection)):
        result = db.claim_message(
            message_id="message-1",
            event_type="nomination.submitted",
            nomination_id=13881,
            processed_at=now,
        )
    return result, connection


def test_only_success_is_skipped():
    now = datetime.now(timezone.utc)
    result, connection = _claim(("success", now), now)

    assert result is True
    assert connection.commits == 0


def test_error_is_atomically_reclaimed_for_retry():
    now = datetime.now(timezone.utc)
    result, connection = _claim(("error", now - timedelta(seconds=1)), now)

    assert result is False
    assert connection.commits == 1
    assert any("SET EventType" in sql for sql, _ in connection.cursor_value.calls)


def test_fresh_pending_claim_is_not_completed_or_reclaimed():
    now = datetime.now(timezone.utc)
    with pytest.raises(db.MessageClaimInProgress):
        _claim(("pending", now - timedelta(seconds=1)), now)


def test_stale_pending_claim_is_recovered():
    now = datetime.now(timezone.utc)
    stale_at = now - timedelta(seconds=db._PENDING_CLAIM_TIMEOUT_SECONDS + 1)
    result, connection = _claim(("pending", stale_at.replace(tzinfo=None)), now)

    assert result is False
    assert connection.commits == 1
