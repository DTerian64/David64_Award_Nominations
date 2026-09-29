"""Orchestration tests for the tenant-major coordinated analytics runner."""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_job
from utils.integrity_analytics_coordinator import LeaseLostError
from utils.stage_result import TenantStageResult


RUN_ID = "11111111-1111-1111-1111-111111111111"
WORKER_ID = "22222222-2222-2222-2222-222222222222"
DATA_AS_OF_UTC = datetime(2026, 9, 29, 2, 10, 11, tzinfo=timezone.utc)


class _ClaimCoordinator:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.started = []
        self.finished = []
        self.completed = []
        self.skipped = []

    def tenant_is_enabled(self, tenant_id):
        return self.enabled

    def mark_tenant_skipped_disabled(self, run_id, tenant_id, worker_id):
        self.skipped.append((run_id, tenant_id, worker_id))

    def heartbeat_tenant(self, run_id, tenant_id, worker_id):
        return True

    def start_stage_attempt(
        self, run_id, tenant_id, worker_id, stage, *, stage_run_id
    ):
        attempt = SimpleNamespace(
            stage_attempt_id=len(self.started) + 1,
            attempt_number=1,
        )
        self.started.append((stage, stage_run_id))
        return attempt

    def finish_stage_attempt(
        self, run_id, tenant_id, worker_id, attempt_id, status, **kwargs
    ):
        self.finished.append((attempt_id, status, kwargs))

    def complete_tenant(
        self, run_id, tenant_id, worker_id, status, *, failure_detail=None
    ):
        self.completed.append((status, failure_detail))


def _claim():
    return SimpleNamespace(
        run_id=RUN_ID,
        tenant_id=7,
        worker_id=WORKER_ID,
        attempt_count=1,
        reclaimed=False,
    )


def test_stage_run_ids_are_stable_per_tenant_and_stage():
    first = run_job._stage_run_id(RUN_ID, 7, "GRAPH")

    assert first == run_job._stage_run_id(RUN_ID, 7, "GRAPH")
    assert first != run_job._stage_run_id(RUN_ID, 8, "GRAPH")
    assert first != run_job._stage_run_id(RUN_ID, 7, "GNN")
    assert uuid.UUID(first)


def test_disabled_tenant_is_skipped_before_any_stage(monkeypatch):
    coordinator = _ClaimCoordinator(enabled=False)
    monkeypatch.setattr(
        run_job,
        "run_tenant_stage",
        lambda *args, **kwargs: pytest.fail("disabled tenant ran a stage"),
    )

    run_job._process_claim(
        coordinator,
        _claim(),
        data_as_of_utc=DATA_AS_OF_UTC,
        heartbeat_seconds=60,
    )

    assert coordinator.skipped == [(RUN_ID, 7, WORKER_ID)]
    assert coordinator.started == []


def test_stage_failure_does_not_block_later_tenant_stages(monkeypatch):
    coordinator = _ClaimCoordinator()

    def run_stage(
        stage,
        tenant_id,
        stage_run_id,
        data_as_of_utc,
        lease_guard,
        lease_fence,
    ):
        assert tenant_id == 7
        assert data_as_of_utc == DATA_AS_OF_UTC
        lease_guard()
        if stage["stage"] == "TABULAR":
            return TenantStageResult.skipped("NO_VALID_CANDIDATE")
        if stage["stage"] == "GNN":
            raise RuntimeError("training exploded")
        return TenantStageResult.succeeded(
            published_version="v1" if stage["stage"] == "GRAPH" else None
        )

    monkeypatch.setattr(run_job, "run_tenant_stage", run_stage)

    run_job._process_claim(
        coordinator,
        _claim(),
        data_as_of_utc=DATA_AS_OF_UTC,
        heartbeat_seconds=60,
    )

    assert [row[0] for row in coordinator.started] == [
        "GRAPH",
        "TABULAR",
        "GNN",
        "FORECAST",
    ]
    assert [row[1] for row in coordinator.finished] == [
        "SUCCEEDED",
        "SKIPPED",
        "FAILED",
        "SUCCEEDED",
    ]
    assert coordinator.completed[0][0] == "FAILED"
    assert "GNN: training exploded" in coordinator.completed[0][1]


def test_lost_lease_leaves_attempt_for_reclaimer(monkeypatch):
    coordinator = _ClaimCoordinator()

    def lose_lease(*args, **kwargs):
        raise LeaseLostError("reclaimed")

    monkeypatch.setattr(run_job, "run_tenant_stage", lose_lease)

    run_job._process_claim(
        coordinator,
        _claim(),
        data_as_of_utc=DATA_AS_OF_UTC,
        heartbeat_seconds=60,
    )

    assert len(coordinator.started) == 1
    assert coordinator.finished == []
    assert coordinator.completed == []


def test_barrier_finalizer_refreshes_cache_once_when_tabular_published(monkeypatch):
    calls = []

    class Coordinator:
        def get_result(self, run_id):
            return None

        def try_claim_tenant(self, run_id, worker_id):
            return None

        def get_run_state(self, run_id):
            return SimpleNamespace(
                result_status="RUNNING",
                pending_tenants=0,
                running_tenants=0,
                barrier_ready=True,
            )

        def try_begin_finalization(self, run_id, worker_id):
            return True

        def build_run_summary(self, run_id):
            return {
                "failed_tenants": 0,
                "tabular_model_published": True,
            }

        def complete_finalization(
            self, run_id, worker_id, result, *, summary, failure_detail
        ):
            calls.append((result, summary, failure_detail))

    monkeypatch.setattr(run_job, "notify_api_refresh", lambda: calls.append("refresh"))

    result = run_job._run_claim_loop(
        Coordinator(),
        RUN_ID,
        WORKER_ID,
        data_as_of_utc=DATA_AS_OF_UTC,
        heartbeat_seconds=60,
        poll_seconds=0.001,
    )

    assert result == "SUCCEEDED"
    assert calls[0] == "refresh"
    assert calls[1][0] == "SUCCEEDED"


def test_preparation_leader_runs_global_work_before_queue(monkeypatch):
    events = []

    class Coordinator:
        def get_run_state(self, run_id):
            return SimpleNamespace(
                preparation_status="PENDING",
                result_status="RUNNING",
            )

        def try_begin_preparation(self, run_id, worker_id):
            return True

        def heartbeat_preparation(self, run_id, worker_id):
            return True

        def initialize_tenant_queue(self, run_id, worker_id):
            events.append("queue")
            return 4

        def mark_preparation_ready(self, run_id, worker_id):
            events.append("ready")

    def prepare(guard):
        guard()
        events.append("global")

    monkeypatch.setattr(run_job, "run_global_preparation", prepare)

    assert run_job._prepare_execution(
        Coordinator(),
        RUN_ID,
        WORKER_ID,
        heartbeat_seconds=60,
        poll_seconds=0.001,
    )
    assert events == ["global", "queue", "ready"]


def test_lease_guard_fails_closed_when_renewal_rejects_owner():
    heartbeat = run_job.LeaseHeartbeat(
        lambda: False,
        interval_seconds=60,
        label="test",
    )

    with pytest.raises(LeaseLostError):
        heartbeat.assert_owned()
