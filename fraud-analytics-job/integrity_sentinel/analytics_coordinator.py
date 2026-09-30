"""Azure SQL coordination for parallel Integrity Sentinel job replicas.

The coordinator deliberately owns no analytics orchestration.  It provides the
transactional operations that Phase 3's tenant-major runner will compose:
execution registration, preparation/finalization leadership, tenant leases,
and append-oriented stage-attempt history.

Every operation opens its own connection.  In particular, lease heartbeats can
run on a background thread without sharing a pyodbc connection with a long
analytics stage.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable


ACTOR = "svc:fraud-analytics-job"
STAGES = frozenset({"GRAPH", "TABULAR", "GNN", "FORECAST"})
STAGE_RESULTS = frozenset({"SUCCEEDED", "FAILED", "SKIPPED"})
TENANT_RESULTS = frozenset({"SUCCEEDED", "FAILED"})
RUN_RESULTS = frozenset({"SUCCEEDED", "FAILED"})


class CoordinationError(RuntimeError):
    """Base class for coordination failures."""


class LeaseLostError(CoordinationError):
    """Raised when a stale worker tries to mutate lease-owned state."""


@dataclass(frozen=True)
class JobRun:
    run_id: str
    execution_name: str
    data_as_of_utc: datetime
    preparation_status: str
    finalization_status: str
    result_status: str


@dataclass(frozen=True)
class TenantClaim:
    run_id: str
    tenant_id: int
    worker_id: str
    attempt_count: int
    reclaimed: bool


@dataclass(frozen=True)
class StageAttempt:
    stage_attempt_id: int
    attempt_number: int


@dataclass(frozen=True)
class RunState:
    preparation_status: str
    finalization_status: str
    result_status: str
    pending_tenants: int
    running_tenants: int
    reclaimable_tenants: int
    terminal_tenants: int
    failed_tenants: int

    @property
    def barrier_ready(self) -> bool:
        return (
            self.preparation_status == "READY"
            and self.pending_tenants == 0
            and self.running_tenants == 0
        )


REGISTER_EXECUTION_SQL = """
SET NOCOUNT ON;
MERGE ops.IntegrityAnalyticsJobRuns WITH (HOLDLOCK) AS target
USING (SELECT ? AS ExecutionName, ? AS JobName) AS source
    ON target.ExecutionName = source.ExecutionName
WHEN MATCHED THEN UPDATE SET
    JobName = source.JobName,
    updated_at = SYSUTCDATETIME(),
    updated_by = ?
WHEN NOT MATCHED THEN INSERT (
    ExecutionName, JobName, created_by, updated_by
) VALUES (
    source.ExecutionName, source.JobName, ?, ?
)
OUTPUT inserted.RunId, inserted.ExecutionName, inserted.PreparationStatus,
       inserted.FinalizationStatus, inserted.ResultStatus,
       inserted.DataAsOfUtc;
"""


TRY_BEGIN_PREPARATION_SQL = """
SET NOCOUNT ON;
UPDATE ops.IntegrityAnalyticsJobRuns WITH (UPDLOCK, ROWLOCK)
SET PreparationStatus = 'RUNNING',
    PreparationWorkerId = ?,
    PreparationLeaseExpiresAt = DATEADD(SECOND, ?, SYSUTCDATETIME()),
    PreparationLastHeartbeatAt = SYSUTCDATETIME(),
    FailureDetail = CASE WHEN PreparationStatus = 'FAILED' THEN FailureDetail ELSE NULL END,
    updated_at = SYSUTCDATETIME(),
    updated_by = ?
OUTPUT inserted.RunId
WHERE RunId = ?
  AND ResultStatus = 'RUNNING'
  AND (
      PreparationStatus = 'PENDING'
      OR (
          PreparationStatus = 'RUNNING'
          AND PreparationLeaseExpiresAt < SYSUTCDATETIME()
      )
  );
"""


INITIALIZE_TENANT_QUEUE_SQL = """
SET NOCOUNT ON;
INSERT INTO ops.IntegrityAnalyticsTenantRuns (
    RunId, TenantId, Status, created_by, updated_by
)
SELECT ?, tenant.TenantId, 'PENDING', ?, ?
FROM OPENJSON(?) WITH (TenantId INT '$') AS tenant
WHERE tenant.TenantId IS NOT NULL
AND NOT EXISTS (
    SELECT 1
    FROM ops.IntegrityAnalyticsTenantRuns AS existing WITH (UPDLOCK, HOLDLOCK)
    WHERE existing.RunId = ? AND existing.TenantId = tenant.TenantId
);
SELECT @@ROWCOUNT;
"""


CLAIM_TENANT_SQL = """
SET NOCOUNT ON;
DECLARE @Candidate TABLE (
    TenantId INT PRIMARY KEY,
    PreviousStatus VARCHAR(32) NOT NULL
);

INSERT INTO @Candidate (TenantId, PreviousStatus)
SELECT TOP (1) TenantId, Status
FROM ops.IntegrityAnalyticsTenantRuns WITH (UPDLOCK, READPAST, ROWLOCK)
WHERE RunId = ?
  AND (
      Status = 'PENDING'
      OR (
          Status = 'RUNNING'
          AND LeaseExpiresAt < SYSUTCDATETIME()
      )
  )
ORDER BY CASE WHEN Status = 'RUNNING' THEN 0 ELSE 1 END,
         LeaseExpiresAt,
         TenantId;

UPDATE attempt
SET Status = 'ABANDONED',
    CompletedAt = SYSUTCDATETIME(),
    DurationSeconds =
        CONVERT(DECIMAL(18,3), DATEDIFF_BIG(MILLISECOND, attempt.StartedAt, SYSUTCDATETIME())) / 1000,
    ReasonCode = 'LEASE_RECLAIMED',
    FailureDetail = COALESCE(attempt.FailureDetail, N'Worker lease expired; tenant reclaimed.'),
    updated_at = SYSUTCDATETIME(),
    updated_by = ?
FROM ops.IntegrityAnalyticsStageAttempts AS attempt
JOIN @Candidate AS candidate ON candidate.TenantId = attempt.TenantId
WHERE attempt.RunId = ?
  AND candidate.PreviousStatus = 'RUNNING'
  AND attempt.Status = 'RUNNING';

UPDATE tenant_run
SET Status = 'RUNNING',
    WorkerId = ?,
    LeaseExpiresAt = DATEADD(SECOND, ?, SYSUTCDATETIME()),
    LastHeartbeatAt = SYSUTCDATETIME(),
    AttemptCount = AttemptCount + 1,
    CurrentStage = NULL,
    StartedAt = COALESCE(StartedAt, SYSUTCDATETIME()),
    CompletedAt = NULL,
    FailureDetail = NULL,
    updated_at = SYSUTCDATETIME(),
    updated_by = ?
OUTPUT inserted.RunId, inserted.TenantId, inserted.WorkerId,
       inserted.AttemptCount, candidate.PreviousStatus
FROM ops.IntegrityAnalyticsTenantRuns AS tenant_run
JOIN @Candidate AS candidate ON candidate.TenantId = tenant_run.TenantId
WHERE tenant_run.RunId = ?;
"""


START_STAGE_ATTEMPT_SQL = """
SET NOCOUNT ON;
DECLARE @Owned TABLE (TenantId INT PRIMARY KEY);
UPDATE ops.IntegrityAnalyticsTenantRuns WITH (UPDLOCK, ROWLOCK)
SET CurrentStage = ?, updated_at = SYSUTCDATETIME(), updated_by = ?
OUTPUT inserted.TenantId INTO @Owned
WHERE RunId = ? AND TenantId = ? AND WorkerId = ?
  AND Status = 'RUNNING' AND LeaseExpiresAt > SYSUTCDATETIME();

IF NOT EXISTS (SELECT 1 FROM @Owned)
BEGIN
    THROW 51001, 'Tenant lease is not owned by this worker.', 1;
END;

DECLARE @AttemptNumber INT;
SELECT @AttemptNumber = ISNULL(MAX(AttemptNumber), 0) + 1
FROM ops.IntegrityAnalyticsStageAttempts WITH (UPDLOCK, HOLDLOCK)
WHERE RunId = ? AND TenantId = ? AND Stage = ?;

INSERT INTO ops.IntegrityAnalyticsStageAttempts (
    RunId, TenantId, WorkerId, Stage, AttemptNumber, Status,
    StageRunId, created_by, updated_by
)
OUTPUT inserted.StageAttemptId, inserted.AttemptNumber
VALUES (?, ?, ?, ?, @AttemptNumber, 'RUNNING', ?, ?, ?);
"""


RUN_STATE_SQL = """
SET NOCOUNT ON;
SELECT job.PreparationStatus, job.FinalizationStatus, job.ResultStatus,
       COALESCE(SUM(CASE WHEN tenant.Status = 'PENDING' THEN 1 ELSE 0 END), 0),
       COALESCE(SUM(CASE WHEN tenant.Status = 'RUNNING' THEN 1 ELSE 0 END), 0),
       COALESCE(SUM(CASE WHEN tenant.Status = 'RUNNING'
                          AND tenant.LeaseExpiresAt < SYSUTCDATETIME()
                         THEN 1 ELSE 0 END), 0),
       COALESCE(SUM(CASE WHEN tenant.Status IN (
                            'SUCCEEDED','FAILED','SKIPPED_DISABLED','SKIPPED_NO_WORK'
                         ) THEN 1 ELSE 0 END), 0),
       COALESCE(SUM(CASE WHEN tenant.Status = 'FAILED' THEN 1 ELSE 0 END), 0)
FROM ops.IntegrityAnalyticsJobRuns AS job
LEFT JOIN ops.IntegrityAnalyticsTenantRuns AS tenant ON tenant.RunId = job.RunId
WHERE job.RunId = ?
GROUP BY job.PreparationStatus, job.FinalizationStatus, job.ResultStatus;
"""


TRY_BEGIN_FINALIZATION_SQL = """
SET NOCOUNT ON;
UPDATE job WITH (UPDLOCK, ROWLOCK)
SET FinalizationStatus = 'RUNNING',
    FinalizationWorkerId = ?,
    FinalizationLeaseExpiresAt = DATEADD(SECOND, ?, SYSUTCDATETIME()),
    FinalizationLastHeartbeatAt = SYSUTCDATETIME(),
    updated_at = SYSUTCDATETIME(),
    updated_by = ?
OUTPUT inserted.RunId
FROM ops.IntegrityAnalyticsJobRuns AS job
WHERE job.RunId = ?
  AND job.PreparationStatus = 'READY'
  AND job.ResultStatus = 'RUNNING'
  AND (
      job.FinalizationStatus = 'PENDING'
      OR (
          job.FinalizationStatus = 'RUNNING'
          AND job.FinalizationLeaseExpiresAt < SYSUTCDATETIME()
      )
  )
  AND NOT EXISTS (
      SELECT 1 FROM ops.IntegrityAnalyticsTenantRuns AS tenant
      WHERE tenant.RunId = job.RunId
        AND tenant.Status IN ('PENDING','RUNNING')
  );
"""


class IntegrityAnalyticsCoordinator:
    """Transactional coordination facade used independently by each replica."""

    def __init__(
        self,
        connection_factory: Callable[[], Any] | None = None,
        *,
        actor: str = ACTOR,
        lease_seconds: int = 300,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if connection_factory is None:
            from integrity_sentinel.db import connect

            connection_factory = connect
        self._connection_factory = connection_factory
        self.actor = _bounded(actor, 256) or ACTOR
        self.lease_seconds = lease_seconds

    def register_execution(self, execution_name: str, job_name: str) -> JobRun:
        row = self._execute_one(
            REGISTER_EXECUTION_SQL,
            (
                _required(execution_name, "execution_name", 256),
                _required(job_name, "job_name", 256),
                self.actor,
                self.actor,
                self.actor,
            ),
        )
        if row is None:
            raise CoordinationError("Execution registration returned no row")
        return JobRun(
            run_id=str(row[0]),
            execution_name=str(row[1]),
            preparation_status=str(row[2]),
            finalization_status=str(row[3]),
            result_status=str(row[4]),
            data_as_of_utc=row[5],
        )

    def try_begin_preparation(self, run_id: str, worker_id: str) -> bool:
        return self._execute_one(
            TRY_BEGIN_PREPARATION_SQL,
            (_uuid(worker_id), self.lease_seconds, self.actor, _uuid(run_id)),
        ) is not None

    def heartbeat_preparation(self, run_id: str, worker_id: str) -> bool:
        row = self._execute_one(
            """
            SET NOCOUNT ON;
            UPDATE ops.IntegrityAnalyticsJobRuns WITH (ROWLOCK)
            SET PreparationLeaseExpiresAt = DATEADD(SECOND, ?, SYSUTCDATETIME()),
                PreparationLastHeartbeatAt = SYSUTCDATETIME(),
                updated_at = SYSUTCDATETIME(), updated_by = ?
            OUTPUT inserted.RunId
            WHERE RunId = ? AND PreparationStatus = 'RUNNING'
              AND PreparationWorkerId = ?
              AND PreparationLeaseExpiresAt > SYSUTCDATETIME();
            """,
            (self.lease_seconds, self.actor, _uuid(run_id), _uuid(worker_id)),
        )
        return row is not None

    def initialize_tenant_queue(
        self,
        run_id: str,
        worker_id: str,
        tenant_ids: list[int],
    ) -> int:
        """Insert currently enabled tenants while the caller owns preparation."""
        normalized_tenant_ids = sorted({int(value) for value in tenant_ids})
        if any(value <= 0 for value in normalized_tenant_ids):
            raise ValueError("tenant_ids must contain only positive identifiers")
        row = self._execute_one(
            """
            SET NOCOUNT ON;
            IF NOT EXISTS (
                SELECT 1 FROM ops.IntegrityAnalyticsJobRuns WITH (UPDLOCK, ROWLOCK)
                WHERE RunId = ? AND PreparationStatus = 'RUNNING'
                  AND PreparationWorkerId = ?
                  AND PreparationLeaseExpiresAt > SYSUTCDATETIME()
            )
            BEGIN
                THROW 51001, 'Preparation lease is not owned by this worker.', 1;
            END;
            """ + INITIALIZE_TENANT_QUEUE_SQL,
            (
                _uuid(run_id),
                _uuid(worker_id),
                _uuid(run_id),
                self.actor,
                self.actor,
                json.dumps(normalized_tenant_ids, separators=(",", ":")),
                _uuid(run_id),
            ),
        )
        return int(row[0]) if row else 0

    def mark_preparation_ready(self, run_id: str, worker_id: str) -> None:
        self._require_row(
            """
            SET NOCOUNT ON;
            UPDATE ops.IntegrityAnalyticsJobRuns WITH (ROWLOCK)
            SET PreparationStatus = 'READY',
                PreparationWorkerId = NULL,
                PreparationLeaseExpiresAt = NULL,
                updated_at = SYSUTCDATETIME(), updated_by = ?
            OUTPUT inserted.RunId
            WHERE RunId = ? AND PreparationStatus = 'RUNNING'
              AND PreparationWorkerId = ?
              AND PreparationLeaseExpiresAt > SYSUTCDATETIME();
            """,
            (self.actor, _uuid(run_id), _uuid(worker_id)),
            "Preparation lease was lost before it could be completed",
        )

    def mark_preparation_failed(
        self, run_id: str, worker_id: str, failure_detail: str | None
    ) -> None:
        self._require_row(
            """
            SET NOCOUNT ON;
            UPDATE ops.IntegrityAnalyticsJobRuns WITH (ROWLOCK)
            SET PreparationStatus = 'FAILED',
                PreparationWorkerId = NULL,
                PreparationLeaseExpiresAt = NULL,
                FinalizationStatus = 'COMPLETE',
                ResultStatus = 'FAILED',
                CompletedAt = SYSUTCDATETIME(),
                FailureDetail = ?,
                updated_at = SYSUTCDATETIME(), updated_by = ?
            OUTPUT inserted.RunId
            WHERE RunId = ? AND PreparationStatus = 'RUNNING'
              AND PreparationWorkerId = ?
              AND PreparationLeaseExpiresAt > SYSUTCDATETIME();
            """,
            (
                _bounded(failure_detail, 2000),
                self.actor,
                _uuid(run_id),
                _uuid(worker_id),
            ),
            "Preparation lease was lost before failure could be recorded",
        )

    def try_claim_tenant(self, run_id: str, worker_id: str) -> TenantClaim | None:
        row = self._execute_one(
            CLAIM_TENANT_SQL,
            (
                _uuid(run_id),
                self.actor,
                _uuid(run_id),
                _uuid(worker_id),
                self.lease_seconds,
                self.actor,
                _uuid(run_id),
            ),
        )
        if row is None:
            return None
        return TenantClaim(
            run_id=str(row[0]),
            tenant_id=int(row[1]),
            worker_id=str(row[2]),
            attempt_count=int(row[3]),
            reclaimed=str(row[4]) == "RUNNING",
        )

    def heartbeat_tenant(self, run_id: str, tenant_id: int, worker_id: str) -> bool:
        row = self._execute_one(
            """
            SET NOCOUNT ON;
            UPDATE ops.IntegrityAnalyticsTenantRuns WITH (ROWLOCK)
            SET LeaseExpiresAt = DATEADD(SECOND, ?, SYSUTCDATETIME()),
                LastHeartbeatAt = SYSUTCDATETIME(),
                updated_at = SYSUTCDATETIME(), updated_by = ?
            OUTPUT inserted.TenantId
            WHERE RunId = ? AND TenantId = ? AND Status = 'RUNNING'
              AND WorkerId = ? AND LeaseExpiresAt > SYSUTCDATETIME();
            """,
            (
                self.lease_seconds,
                self.actor,
                _uuid(run_id),
                int(tenant_id),
                _uuid(worker_id),
            ),
        )
        return row is not None

    def owns_tenant_lease(self, run_id: str, tenant_id: int, worker_id: str) -> bool:
        row = self._execute_one(
            """
            SET NOCOUNT ON;
            SELECT CASE WHEN EXISTS (
                SELECT 1 FROM ops.IntegrityAnalyticsTenantRuns
                WHERE RunId = ? AND TenantId = ? AND Status = 'RUNNING'
                  AND WorkerId = ? AND LeaseExpiresAt > SYSUTCDATETIME()
            ) THEN 1 ELSE 0 END;
            """,
            (_uuid(run_id), int(tenant_id), _uuid(worker_id)),
            commit=False,
        )
        return bool(row and row[0])

    def fence_tenant_lease(
        self,
        connection,
        run_id: str,
        tenant_id: int,
        worker_id: str,
    ) -> None:
        """Lock the owned lease until the caller commits its publication.

        The caller supplies the same connection used for the visible stage
        write. ``UPDLOCK``/``HOLDLOCK`` prevents a reclaimer from changing the
        tenant row between this ownership check and that transaction's commit.
        """
        row = connection.cursor().execute(
            """
            SELECT TenantId
            FROM ops.IntegrityAnalyticsTenantRuns WITH (UPDLOCK, HOLDLOCK, ROWLOCK)
            WHERE RunId = ? AND TenantId = ? AND Status = 'RUNNING'
              AND WorkerId = ? AND LeaseExpiresAt > SYSUTCDATETIME();
            """,
            (_uuid(run_id), int(tenant_id), _uuid(worker_id)),
        ).fetchone()
        if row is None:
            raise LeaseLostError(
                f"Tenant {tenant_id} lease is no longer owned by worker {worker_id}"
            )

    def mark_tenant_skipped_disabled(
        self, run_id: str, tenant_id: int, worker_id: str
    ) -> None:
        self._finish_tenant(
            run_id,
            tenant_id,
            worker_id,
            "SKIPPED_DISABLED",
            "Tenant disabled before analytics work began.",
        )

    def start_stage_attempt(
        self,
        run_id: str,
        tenant_id: int,
        worker_id: str,
        stage: str,
        *,
        stage_run_id: str | None = None,
    ) -> StageAttempt:
        stage = _stage(stage)
        row = self._execute_one(
            START_STAGE_ATTEMPT_SQL,
            (
                stage,
                self.actor,
                _uuid(run_id),
                int(tenant_id),
                _uuid(worker_id),
                _uuid(run_id),
                int(tenant_id),
                stage,
                _uuid(run_id),
                int(tenant_id),
                _uuid(worker_id),
                stage,
                _bounded(stage_run_id, 128),
                self.actor,
                self.actor,
            ),
        )
        if row is None:
            raise CoordinationError("Stage attempt insert returned no row")
        return StageAttempt(int(row[0]), int(row[1]))

    def finish_stage_attempt(
        self,
        run_id: str,
        tenant_id: int,
        worker_id: str,
        stage_attempt_id: int,
        status: str,
        *,
        reason_code: str | None = None,
        failure_detail: str | None = None,
        published_version: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        status = status.upper()
        if status not in STAGE_RESULTS:
            raise ValueError(f"Unsupported stage result: {status}")
        self._require_row(
            """
            SET NOCOUNT ON;
            UPDATE attempt WITH (ROWLOCK)
            SET Status = ?, CompletedAt = SYSUTCDATETIME(),
                DurationSeconds =
                    CONVERT(DECIMAL(18,3), DATEDIFF_BIG(
                        MILLISECOND, attempt.StartedAt, SYSUTCDATETIME()
                    )) / 1000,
                ReasonCode = ?, FailureDetail = ?, PublishedVersion = ?,
                DiagnosticsJson = ?,
                updated_at = SYSUTCDATETIME(), updated_by = ?
            OUTPUT inserted.StageAttemptId
            FROM ops.IntegrityAnalyticsStageAttempts AS attempt
            JOIN ops.IntegrityAnalyticsTenantRuns AS tenant
              ON tenant.RunId = attempt.RunId AND tenant.TenantId = attempt.TenantId
            WHERE attempt.StageAttemptId = ? AND attempt.RunId = ?
              AND attempt.TenantId = ? AND attempt.WorkerId = ?
              AND attempt.Status = 'RUNNING'
              AND tenant.Status = 'RUNNING' AND tenant.WorkerId = ?
              AND tenant.LeaseExpiresAt > SYSUTCDATETIME();
            """,
            (
                status,
                _bounded(reason_code, 64),
                _bounded(failure_detail, 2000),
                _bounded(published_version, 128),
                _diagnostics_json(diagnostics),
                self.actor,
                int(stage_attempt_id),
                _uuid(run_id),
                int(tenant_id),
                _uuid(worker_id),
                _uuid(worker_id),
            ),
            "Tenant lease was lost before the stage attempt could be completed",
        )

    def complete_tenant(
        self,
        run_id: str,
        tenant_id: int,
        worker_id: str,
        status: str,
        *,
        failure_detail: str | None = None,
    ) -> None:
        status = status.upper()
        if status not in TENANT_RESULTS:
            raise ValueError(f"Unsupported tenant result: {status}")
        self._finish_tenant(run_id, tenant_id, worker_id, status, failure_detail)

    def get_run_state(self, run_id: str) -> RunState:
        row = self._execute_one(RUN_STATE_SQL, (_uuid(run_id),), commit=False)
        if row is None:
            raise CoordinationError(f"Analytics run {run_id} does not exist")
        return RunState(
            preparation_status=str(row[0]),
            finalization_status=str(row[1]),
            result_status=str(row[2]),
            pending_tenants=int(row[3]),
            running_tenants=int(row[4]),
            reclaimable_tenants=int(row[5]),
            terminal_tenants=int(row[6]),
            failed_tenants=int(row[7]),
        )

    def try_begin_finalization(self, run_id: str, worker_id: str) -> bool:
        return self._execute_one(
            TRY_BEGIN_FINALIZATION_SQL,
            (_uuid(worker_id), self.lease_seconds, self.actor, _uuid(run_id)),
        ) is not None

    def build_run_summary(self, run_id: str) -> dict[str, int | bool]:
        """Return bounded final counts and whether a Tabular model was published."""
        run_id = _uuid(run_id)
        row = self._execute_one(
            """
            SET NOCOUNT ON;
            SELECT
                COUNT_BIG(*),
                COALESCE(SUM(CASE WHEN Status = 'SUCCEEDED' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN Status = 'FAILED' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN Status = 'SKIPPED_DISABLED' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN Status = 'SKIPPED_NO_WORK' THEN 1 ELSE 0 END), 0),
                (SELECT COUNT_BIG(*)
                 FROM ops.IntegrityAnalyticsStageAttempts WHERE RunId = ?),
                (SELECT COUNT_BIG(*)
                 FROM ops.IntegrityAnalyticsStageAttempts
                 WHERE RunId = ? AND Status = 'FAILED'),
                (SELECT COUNT_BIG(*)
                 FROM ops.IntegrityAnalyticsStageAttempts
                 WHERE RunId = ? AND Stage = 'TABULAR'
                   AND Status = 'SUCCEEDED' AND PublishedVersion IS NOT NULL)
            FROM ops.IntegrityAnalyticsTenantRuns
            WHERE RunId = ?;
            """,
            (run_id, run_id, run_id, run_id),
            commit=False,
        )
        if row is None:
            raise CoordinationError(f"Could not summarize analytics run {run_id}")
        return {
            "tenant_count": int(row[0]),
            "succeeded_tenants": int(row[1]),
            "failed_tenants": int(row[2]),
            "skipped_disabled_tenants": int(row[3]),
            "skipped_no_work_tenants": int(row[4]),
            "stage_attempt_count": int(row[5]),
            "failed_stage_attempts": int(row[6]),
            "tabular_model_published": int(row[7]) > 0,
        }

    def heartbeat_finalization(self, run_id: str, worker_id: str) -> bool:
        row = self._execute_one(
            """
            SET NOCOUNT ON;
            UPDATE ops.IntegrityAnalyticsJobRuns WITH (ROWLOCK)
            SET FinalizationLeaseExpiresAt = DATEADD(SECOND, ?, SYSUTCDATETIME()),
                FinalizationLastHeartbeatAt = SYSUTCDATETIME(),
                updated_at = SYSUTCDATETIME(), updated_by = ?
            OUTPUT inserted.RunId
            WHERE RunId = ? AND FinalizationStatus = 'RUNNING'
              AND FinalizationWorkerId = ?
              AND FinalizationLeaseExpiresAt > SYSUTCDATETIME();
            """,
            (self.lease_seconds, self.actor, _uuid(run_id), _uuid(worker_id)),
        )
        return row is not None

    def complete_finalization(
        self,
        run_id: str,
        worker_id: str,
        result_status: str,
        *,
        summary: dict[str, Any] | None = None,
        failure_detail: str | None = None,
    ) -> None:
        result_status = result_status.upper()
        if result_status not in RUN_RESULTS:
            raise ValueError(f"Unsupported run result: {result_status}")
        self._require_row(
            """
            SET NOCOUNT ON;
            UPDATE ops.IntegrityAnalyticsJobRuns WITH (ROWLOCK)
            SET FinalizationStatus = 'COMPLETE',
                FinalizationWorkerId = NULL,
                FinalizationLeaseExpiresAt = NULL,
                ResultStatus = ?, CompletedAt = SYSUTCDATETIME(),
                SummaryJson = ?, FailureDetail = ?,
                updated_at = SYSUTCDATETIME(), updated_by = ?
            OUTPUT inserted.RunId
            WHERE RunId = ? AND FinalizationStatus = 'RUNNING'
              AND FinalizationWorkerId = ?
              AND FinalizationLeaseExpiresAt > SYSUTCDATETIME();
            """,
            (
                result_status,
                _diagnostics_json(summary),
                _bounded(failure_detail, 2000),
                self.actor,
                _uuid(run_id),
                _uuid(worker_id),
            ),
            "Finalization lease was lost before the result could be recorded",
        )

    def get_result(self, run_id: str) -> str | None:
        row = self._execute_one(
            """
            SET NOCOUNT ON;
            SELECT ResultStatus
            FROM ops.IntegrityAnalyticsJobRuns
            WHERE RunId = ?;
            """,
            (_uuid(run_id),),
            commit=False,
        )
        if row is None:
            raise CoordinationError(f"Analytics run {run_id} does not exist")
        result = str(row[0])
        return None if result == "RUNNING" else result

    def wait_for_result(
        self, run_id: str, *, timeout_seconds: float, poll_seconds: float = 5.0
    ) -> str:
        if timeout_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("timeout_seconds and poll_seconds must be positive")
        deadline = time.monotonic() + timeout_seconds
        while True:
            result = self.get_result(run_id)
            if result is not None:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Analytics run {run_id} did not finish in time")
            time.sleep(min(poll_seconds, remaining))

    def _finish_tenant(
        self,
        run_id: str,
        tenant_id: int,
        worker_id: str,
        status: str,
        failure_detail: str | None,
    ) -> None:
        self._require_row(
            """
            SET NOCOUNT ON;
            UPDATE ops.IntegrityAnalyticsTenantRuns WITH (ROWLOCK)
            SET Status = ?, WorkerId = ?, LeaseExpiresAt = NULL,
                CurrentStage = NULL, CompletedAt = SYSUTCDATETIME(),
                FailureDetail = ?, updated_at = SYSUTCDATETIME(), updated_by = ?
            OUTPUT inserted.TenantId
            WHERE RunId = ? AND TenantId = ? AND Status = 'RUNNING'
              AND WorkerId = ? AND LeaseExpiresAt > SYSUTCDATETIME();
            """,
            (
                status,
                _uuid(worker_id),
                _bounded(failure_detail, 2000),
                self.actor,
                _uuid(run_id),
                int(tenant_id),
                _uuid(worker_id),
            ),
            "Tenant lease was lost before the tenant result could be recorded",
        )

    def _require_row(self, sql: str, params: tuple[Any, ...], message: str) -> None:
        if self._execute_one(sql, params) is None:
            raise LeaseLostError(message)

    def _execute_one(
        self,
        sql: str,
        params: tuple[Any, ...],
        *,
        commit: bool = True,
    ) -> Any | None:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            cursor.execute(sql, params)
            row = cursor.fetchone()
            if commit:
                connection.commit()
            return row
        except Exception:
            rollback = getattr(connection, "rollback", None)
            if callable(rollback):
                rollback()
            raise
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()


def new_worker_id() -> str:
    """Return the process-local replica identity persisted in lease/history rows."""
    return str(uuid.uuid4())


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid UUID: {value}") from exc


def _required(value: str, name: str, limit: int) -> str:
    bounded = _bounded(value, limit)
    if not bounded:
        raise ValueError(f"{name} is required")
    return bounded


def _bounded(value: object | None, limit: int) -> str | None:
    if value is None:
        return None
    return str(value).replace("\x00", "")[:limit]


def _diagnostics_json(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    encoded = json.dumps(value, separators=(",", ":"), default=str)
    if len(encoded) <= 4000:
        return encoded
    return json.dumps(
        {"truncated": True, "original_length": len(encoded)},
        separators=(",", ":"),
    )


def _stage(value: str) -> str:
    stage = value.upper()
    if stage not in STAGES:
        raise ValueError(f"Unsupported analytics stage: {stage}")
    return stage
