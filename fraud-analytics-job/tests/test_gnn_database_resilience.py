"""Fault-injection contracts for long-running GNN database access."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "integrity-engine-core" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.award_nominations.modeling.gnn import stage as gnn  # noqa: E402
from utils import sql_connection as db_conn  # noqa: E402


class _Connection:
    def __init__(self):
        self.closed = False
        self.commits = 0
        self.rollbacks = 0
        self.cursor_value = object()

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _session():
    connections = []

    def factory():
        connection = _Connection()
        connections.append(connection)
        return connection

    return db_conn.RenewableConnection(
        factory, log_context="Tenant 5 GNN"
    ), connections


def test_read_connection_is_closed_and_publication_opens_fresh_connection():
    session, connections = _session()

    session.cursor()
    session.release()

    assert len(connections) == 1
    assert connections[0].rollbacks == 1
    assert connections[0].closed

    session.cursor()
    assert len(connections) == 2
    assert connections[1] is not connections[0]


def test_transient_publication_failure_retries_on_fresh_connection(monkeypatch):
    session, connections = _session()
    attempts = 0
    monkeypatch.setattr(gnn, "_PUBLICATION_RETRY_DELAY_SECONDS", 0)

    def operation():
        nonlocal attempts
        attempts += 1
        session.cursor()
        if attempts == 1:
            raise RuntimeError("08S01 communication link failure")
        return "published"

    result = gnn._run_publication_with_retry(
        session,
        tenant_id=5,
        operation_name="test-publication",
        operation=operation,
        reconcile=lambda: (False, None),
    )

    assert result == "published"
    assert attempts == 2
    assert len(connections) == 2
    assert connections[0].closed


def test_ambiguous_publication_commit_is_reconciled_without_reexecution(monkeypatch):
    session, connections = _session()
    attempts = 0
    monkeypatch.setattr(gnn, "_PUBLICATION_RETRY_DELAY_SECONDS", 0)

    def operation():
        nonlocal attempts
        attempts += 1
        session.cursor()
        raise RuntimeError("08S01 acknowledgement lost")

    def reconcile():
        session.cursor()
        return True, "already-committed"

    result = gnn._run_publication_with_retry(
        session,
        tenant_id=5,
        operation_name="test-reconciliation",
        operation=operation,
        reconcile=reconcile,
    )

    assert result == "already-committed"
    assert attempts == 1
    assert len(connections) == 2
    assert connections[0].closed


def test_reconciliation_matches_stable_run_and_serving_version():
    class _Cursor:
        def __init__(self):
            self.params = None

        def execute(self, _sql, params):
            self.params = params
            return self

        def fetchone(self):
            return ("SUCCEEDED", "gnn-v4-stable", '{"embedding_count":42}')

    connection = _Connection()
    connection.cursor_value = _Cursor()
    session = db_conn.RenewableConnection(
        lambda: connection, log_context="Tenant 5 GNN"
    )

    matched, diagnostics = gnn._reconcile_gnn_status(
        session,
        tenant_id=5,
        run_id="11111111-1111-1111-1111-111111111111",
        expected_status="SUCCEEDED",
        expected_version="gnn-v4-stable",
    )

    assert matched
    assert diagnostics == {"embedding_count": 42}
    assert connection.cursor_value.params == (
        5,
        "11111111-1111-1111-1111-111111111111",
    )
    assert connection.rollbacks == 1


def test_failure_status_uses_connection_opened_after_compute(monkeypatch):
    connections = []
    status_connections = []

    def factory():
        connection = _Connection()
        connections.append(connection)
        return connection

    def fail_after_compute(connection, *_args):
        connection.cursor()
        connection.release()
        raise RuntimeError("training exploded")

    def persist_status(connection, **_kwargs):
        connection.cursor()
        status_connections.append(connection._connection)
        connection.commit()

    monkeypatch.setattr(gnn, "connect", factory)
    monkeypatch.setattr(gnn, "_process_tenant", fail_after_compute)
    monkeypatch.setattr(gnn, "upsert_component_status", persist_status)
    monkeypatch.setattr(gnn, "_log_peak_rss", lambda _label: None)

    with pytest.raises(RuntimeError, match="training exploded"):
        gnn.process_tenant(5, "11111111-1111-1111-1111-111111111111")

    assert len(connections) == 2
    assert connections[0].closed
    assert status_connections == [connections[1]]
    assert connections[1].commits == 1


def test_sql_connection_enables_odbc_idle_resilience(monkeypatch):
    captured = {}

    class _Credential:
        def get_token(self, _scope):
            return type("Token", (), {"token": "token"})()

    def fake_connect(connection_string, **kwargs):
        captured["connection_string"] = connection_string
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setenv("IS_SQL_SERVER", "example.database.windows.net")
    monkeypatch.setenv("IS_SQL_DATABASE", "analytics")
    monkeypatch.setattr(db_conn, "_credential", _Credential())
    monkeypatch.setattr(db_conn.pyodbc, "connect", fake_connect)

    db_conn.connect_from_environment("IS_SQL_SERVER", "IS_SQL_DATABASE")

    assert "ConnectRetryCount=3;" in captured["connection_string"]
    assert "ConnectRetryInterval=10;" in captured["connection_string"]
