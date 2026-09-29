"""Tests for SQL-backed integrity analytics replica coordination."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.integrity_analytics_coordinator import (
    CLAIM_TENANT_SQL,
    INITIALIZE_TENANT_QUEUE_SQL,
    REGISTER_EXECUTION_SQL,
    RUN_STATE_SQL,
    START_STAGE_ATTEMPT_SQL,
    TRY_BEGIN_FINALIZATION_SQL,
    CoordinationError,
    IntegrityAnalyticsCoordinator,
    LeaseLostError,
    RunState,
    _diagnostics_json,
    new_worker_id,
)


RUN_ID = "11111111-1111-1111-1111-111111111111"
WORKER_ID = "22222222-2222-2222-2222-222222222222"


class _Cursor:
    def __init__(self, row=None, error=None):
        self.row = row
        self.error = error
        self.sql = None
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        if self.error:
            raise self.error
        return self

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row=None, error=None):
        self.cursor_value = _Cursor(row, error)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _coordinator(row=None, error=None):
    connection = _Connection(row, error)
    coordinator = IntegrityAnalyticsCoordinator(lambda: connection)
    return coordinator, connection


def test_registration_is_idempotent_by_execution_name_and_returns_shared_run():
    row = (RUN_ID, "execution-42", "PENDING", "PENDING", "RUNNING")
    coordinator, connection = _coordinator(row)

    run = coordinator.register_execution("execution-42", "analytics-sandbox")

    assert run.run_id == RUN_ID
    assert run.result_status == "RUNNING"
    assert "MERGE ops.IntegrityAnalyticsJobRuns WITH (HOLDLOCK)" in REGISTER_EXECUTION_SQL
    assert "ExecutionName" in REGISTER_EXECUTION_SQL
    assert connection.cursor_value.sql.count("?") == len(connection.cursor_value.params)
    assert connection.committed and connection.closed


def test_claim_uses_skip_locked_queue_semantics_and_maps_reclaimed_work():
    row = (RUN_ID, 7, WORKER_ID, 2, "RUNNING")
    coordinator, connection = _coordinator(row)

    claim = coordinator.try_claim_tenant(RUN_ID, WORKER_ID)

    assert claim is not None
    assert claim.tenant_id == 7
    assert claim.attempt_count == 2
    assert claim.reclaimed is True
    assert "UPDLOCK, READPAST, ROWLOCK" in CLAIM_TENANT_SQL
    assert "TOP (1)" in CLAIM_TENANT_SQL
    assert "LeaseExpiresAt < SYSUTCDATETIME()" in CLAIM_TENANT_SQL
    assert "Status = 'ABANDONED'" in CLAIM_TENANT_SQL
    assert "ReasonCode = 'LEASE_RECLAIMED'" in CLAIM_TENANT_SQL
    assert connection.cursor_value.sql.count("?") == len(connection.cursor_value.params)


def test_no_claim_is_normal_while_another_worker_owns_work():
    coordinator, _ = _coordinator(None)

    assert coordinator.try_claim_tenant(RUN_ID, WORKER_ID) is None


def test_queue_and_enable_check_require_a_strict_json_boolean_false():
    assert "OPENJSON" in INITIALIZE_TENANT_QUEUE_SQL
    assert "setting.[type] = 3" in INITIALIZE_TENANT_QUEUE_SQL
    assert "setting.[value] = 'false'" in INITIALIZE_TENANT_QUEUE_SQL
    assert "ISJSON(tenant.integrity_config)" in INITIALIZE_TENANT_QUEUE_SQL
    assert "fraud_analytics_job" in INITIALIZE_TENANT_QUEUE_SQL


def test_stage_attempt_is_append_oriented_and_requires_current_lease_owner():
    coordinator, connection = _coordinator((91, 3))

    attempt = coordinator.start_stage_attempt(
        RUN_ID, 7, WORKER_ID, "gnn", stage_run_id="gnn-candidate-3"
    )

    assert attempt.stage_attempt_id == 91
    assert attempt.attempt_number == 3
    assert "LeaseExpiresAt > SYSUTCDATETIME()" in START_STAGE_ATTEMPT_SQL
    assert "ISNULL(MAX(AttemptNumber), 0) + 1" in START_STAGE_ATTEMPT_SQL
    assert "INSERT INTO ops.IntegrityAnalyticsStageAttempts" in START_STAGE_ATTEMPT_SQL
    assert connection.cursor_value.sql.count("?") == len(connection.cursor_value.params)


def test_lost_lease_cannot_complete_tenant():
    coordinator, _ = _coordinator(None)

    with pytest.raises(LeaseLostError):
        coordinator.complete_tenant(RUN_ID, 7, WORKER_ID, "SUCCEEDED")


def test_publication_fence_uses_stage_transaction_and_update_lock():
    coordinator, _ = _coordinator()
    connection = _Connection((7,))

    coordinator.fence_tenant_lease(connection, RUN_ID, 7, WORKER_ID)

    assert "UPDLOCK, HOLDLOCK, ROWLOCK" in connection.cursor_value.sql
    assert "LeaseExpiresAt > SYSUTCDATETIME()" in connection.cursor_value.sql
    assert not connection.committed


def test_publication_fence_rejects_stale_worker():
    coordinator, _ = _coordinator()
    connection = _Connection(None)

    with pytest.raises(LeaseLostError):
        coordinator.fence_tenant_lease(connection, RUN_ID, 7, WORKER_ID)


def test_database_failure_rolls_back_and_closes_connection():
    coordinator, connection = _coordinator(error=RuntimeError("deadlock victim"))

    with pytest.raises(RuntimeError, match="deadlock victim"):
        coordinator.try_claim_tenant(RUN_ID, WORKER_ID)

    assert connection.rolled_back
    assert connection.closed
    assert not connection.committed


def test_barrier_waits_for_pending_or_running_tenants():
    waiting = RunState("READY", "PENDING", "RUNNING", 0, 1, 0, 3, 0)
    ready = RunState("READY", "PENDING", "RUNNING", 0, 0, 0, 4, 0)

    assert waiting.barrier_ready is False
    assert ready.barrier_ready is True
    assert "tenant.Status IN ('PENDING','RUNNING')" in TRY_BEGIN_FINALIZATION_SQL
    assert "SUM(CASE WHEN tenant.Status = 'RUNNING'" in RUN_STATE_SQL


def test_final_result_is_shared_and_running_result_is_not_terminal():
    coordinator, _ = _coordinator(("RUNNING",))
    assert coordinator.get_result(RUN_ID) is None

    coordinator, _ = _coordinator(("FAILED",))
    assert coordinator.get_result(RUN_ID) == "FAILED"


def test_run_summary_exposes_finalizer_cache_refresh_signal():
    coordinator, connection = _coordinator((4, 2, 1, 1, 0, 15, 2, 1))

    summary = coordinator.build_run_summary(RUN_ID)

    assert summary == {
        "tenant_count": 4,
        "succeeded_tenants": 2,
        "failed_tenants": 1,
        "skipped_disabled_tenants": 1,
        "skipped_no_work_tenants": 0,
        "stage_attempt_count": 15,
        "failed_stage_attempts": 2,
        "tabular_model_published": True,
    }
    assert connection.cursor_value.sql.count("?") == len(connection.cursor_value.params)


def test_missing_run_and_tenant_are_reported():
    coordinator, _ = _coordinator(None)
    with pytest.raises(CoordinationError, match="does not exist"):
        coordinator.get_run_state(RUN_ID)

    coordinator, _ = _coordinator(None)
    with pytest.raises(CoordinationError, match="Tenant 77"):
        coordinator.tenant_is_enabled(77)


def test_diagnostics_stay_valid_json_when_bounded():
    encoded = _diagnostics_json({"payload": "x" * 5000})

    assert len(encoded) <= 4000
    assert json.loads(encoded) == {"truncated": True, "original_length": 5014}


def test_worker_ids_are_process_local_uuids():
    first = new_worker_id()
    second = new_worker_id()

    assert uuid.UUID(first)
    assert uuid.UUID(second)
    assert first != second
