"""Integrity analytics Container Apps Job entry point.

A normal execution is coordinated through Azure SQL so multiple replicas share
one execution record and dynamically claim tenants. One preparation leader
refreshes the shared Graph tables, performs Graph embedding retention, and
synchronizes holidays. Each claimed tenant then runs Graph, Tabular, GNN, and
Forecast in that order. Stage failures are recorded but do not block later
stages for the same tenant. One finalizer refreshes the backend model cache and
publishes the shared execution result read by every replica.

Filtered ``--only`` or ``--tenant`` invocations retain the standalone harness
for local analysis and operational recovery.
"""


import argparse
import importlib
import logging
import os
import sys
import threading
import time
import urllib.request
import urllib.error
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

# ── Environment ───────────────────────────────────────────────────────────────
# Load .env FIRST — before wake_database() or setup_logging() read os.environ,
# and before any stage module is imported. Same path the stages use, so the
# orchestrated and standalone paths are identical. In Azure Container Apps there
# is no .env file: env vars are injected by the platform and load_dotenv is a
# harmless no-op (and won't override platform values).
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

# ── Logging setup ─────────────────────────────────────────────────────────────
# Structured JSON logging with the 'App_Log: ' prefix on our own records, so they
# can be isolated in Log Analytics with `| where message startswith "App_Log:"`.
# Mirrors backend/logging_config.py. run_job.py is the container entrypoint, so
# its directory is on sys.path[0] and logging_config imports cleanly.
from logging_config import setup_logging  # noqa: E402 - .env configures logging
setup_logging()
logger = logging.getLogger("fraud_analytics_job")

# ── Path setup ───────────────────────────────────────────────────────────────
# WORKDIR in the container is /app, which is also the build context
# (fraud-analytics-job/). Production stages live in the Award system package;
# infrastructure lives in the top-level shared packages. The full job directory
# is copied into the image, so dotted imports need no cross-directory COPY steps.
JOB_DIR = Path(__file__).parent.resolve()   # /app  (same dir as this file)
sys.path.insert(0, str(JOB_DIR))

from integrity_sentinel.analytics_coordinator import (  # noqa: E402
    IntegrityAnalyticsCoordinator,
    LeaseLostError,
    new_worker_id,
)
from integrity_sentinel.db import connect as connect_integrity_sentinel  # noqa: E402
from systems.award_nominations.source.connection import (  # noqa: E402
    connect as connect_award_source,
)
from systems.award_nominations.source.tenant_config import (  # noqa: E402
    get_tenants,
    tenant_is_enabled,
)
from systems.award_nominations.pipeline import (  # noqa: E402
    GLOBAL_PREPARATION_MODULES,
    STANDALONE_STAGES,
    TENANT_STAGES,
)
from utils.stage_result import TenantStageResult  # noqa: E402

# Stage scripts are invoked as modules so they share the same process and
# benefit from any cached state (DB connection pool, loaded model, etc.).
# Each script's __main__ guard is bypassed — we call their main() directly.


def wake_database(
    max_attempts: int = 8,
    attempt_timeout_s: int = 120,
    retry_delay_s: float = 20.0,
) -> None:
    """Wake each distinct source and Sentinel database before stage execution."""

    targets = (
        (
            "Award source",
            os.getenv("AWARD_SQL_SERVER", "(not set)"),
            os.getenv("AWARD_SQL_DATABASE", "(not set)"),
            connect_award_source,
        ),
        (
            "Integrity Sentinel",
            os.getenv("IS_SQL_SERVER", "(not set)"),
            os.getenv("IS_SQL_DATABASE", "(not set)"),
            connect_integrity_sentinel,
        ),
    )
    seen: set[tuple[str, str]] = set()
    for label, server, database, connection_factory in targets:
        address = (server, database)
        if address in seen:
            logger.info("DB WAKE-UP  %s shares the already-awake database", label)
            continue
        seen.add(address)
        logger.info("DB WAKE-UP  %s server=%s database=%s", label, server, database)
        started = time.monotonic()
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            attempt_started = time.monotonic()
            try:
                connection = connection_factory(attempt_timeout_s)
                connection.execute("SELECT 1").fetchone()
                connection.close()
                logger.info(
                    "DB WAKE-UP  ✓ %s is awake (%.1f s, attempts: %d)",
                    label,
                    time.monotonic() - started,
                    attempt,
                )
                break
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "DB WAKE-UP  %s attempt %d/%d failed after %.1f s: %s",
                    label,
                    attempt,
                    max_attempts,
                    time.monotonic() - attempt_started,
                    exc,
                )
                if attempt < max_attempts:
                    time.sleep(retry_delay_s)
        else:
            elapsed = time.monotonic() - started
            raise RuntimeError(
                f"{label} database did not wake up after {max_attempts} attempts "
                f"({elapsed:.0f}s). Last error: {last_exc}"
            )


def notify_api_refresh() -> None:
    """
    POST to /api/internal/refresh-fraud-model so the live backend immediately
    replaces its in-memory model cache with the freshly uploaded pkls.

    This is best-effort: a failure here is logged but does NOT fail the job —
    the backend's TTL eviction will pick up the new models within one eviction
    cycle regardless.

    Requires:
        API_BASE_URL       — e.g. "https://award-api-sandbox.internal.cae-domain"
        FRAUD_ANALYTICS_JOB_WEBHOOK_SECRET — shared secret matching backend FRAUD_ANALYTICS_JOB_WEBHOOK_SECRET
    """
    api_base = os.getenv("API_BASE_URL", "").rstrip("/")
    secret   = os.getenv("FRAUD_ANALYTICS_JOB_WEBHOOK_SECRET", "")

    if not api_base:
        logger.warning(
            "notify_api_refresh: API_BASE_URL not set — skipping cache refresh call. "
            "Backend will refresh via TTL eviction instead."
        )
        return

    url = f"{api_base}/api/internal/refresh-fraud-model"
    logger.info("notify_api_refresh: POST %s", url)

    try:
        req = urllib.request.Request(
            url,
            data=b"",
            method="POST",
            headers={
                "X-Internal-Key": secret,
                "Content-Type":   "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
            logger.info(
                "notify_api_refresh: ✅ status=%s updated=%s",
                body.get("status"), body.get("updated"),
            )
    except urllib.error.HTTPError as exc:
        logger.warning(
            "notify_api_refresh: ⚠️  HTTP %d — %s. "
            "Backend will refresh via TTL eviction.",
            exc.code, exc.reason,
        )
    except Exception as exc:
        logger.warning(
            "notify_api_refresh: ⚠️  Could not reach backend (%s). "
            "Backend will refresh via TTL eviction.",
            exc,
        )


def run_stage(name: str, module_path: str, tenants_to_process: list | None = None) -> bool:
    """
    Import and execute the main() function of a pipeline stage.
    Returns True on success, False on any exception.
    tenants_to_process is forwarded to mod.main() — stages that are not
    tenant-scoped (e.g. sync_holidays) accept but ignore it.
    """
    logger.info("STAGE: %s", name)
    t0 = time.monotonic()
    try:
        import importlib
        mod = importlib.import_module(module_path)
        mod.main(tenants_to_process=tenants_to_process)
        elapsed = time.monotonic() - t0
        logger.info("✓  %s completed in %.1f s", name, elapsed)
        return True
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error("✗  %s FAILED after %.1f s: %s", name, elapsed, exc, exc_info=True)
        return False


# ── Stage registries ─────────────────────────────────────────────────────────
# Full executions use TENANT_STAGES in tenant-major order. STAGES preserves the
# standalone --only/--tenant harness used for local analysis and recovery.
STAGES = [
    {
        **stage,
        "post": notify_api_refresh if stage["key"] == "train_tabular_model" else None,
    }
    for stage in STANDALONE_STAGES
]
_STAGE_KEYS = [s["key"] for s in STAGES]


class LeaseHeartbeat:
    """Renew a SQL lease in the background and expose a publication guard."""

    def __init__(
        self,
        renew: Callable[[], bool],
        *,
        interval_seconds: float,
        label: str,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self._renew = renew
        self._interval_seconds = interval_seconds
        self._label = label
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"lease-heartbeat:{label}",
            daemon=True,
        )

    def __enter__(self) -> "LeaseHeartbeat":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stop.set()
        self._thread.join(timeout=min(self._interval_seconds + 1, 10))

    def assert_owned(self) -> None:
        if self._lost.is_set() or not self._renew():
            self._lost.set()
            raise LeaseLostError(f"Lease lost for {self._label}")

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                if not self._renew():
                    self._lost.set()
                    logger.error("Lease heartbeat lost ownership: %s", self._label)
                    return
            except Exception as exc:
                # A transient SQL failure is not proof of lease loss. Publication
                # guards synchronously recheck ownership before visible writes.
                logger.warning("Lease heartbeat failed for %s: %s", self._label, exc)


def _stage_run_id(run_id: str, tenant_id: int, stage: str) -> str:
    """Stable tenant-stage correlation ID, reused by reclaimed attempts."""
    return str(uuid.uuid5(uuid.UUID(run_id), f"tenant:{tenant_id}:stage:{stage}"))


def run_global_preparation(lease_guard: Callable[[], None]) -> None:
    graph = importlib.import_module(GLOBAL_PREPARATION_MODULES[0])
    holidays = importlib.import_module(GLOBAL_PREPARATION_MODULES[1])
    graph.prepare_global(lease_guard=lease_guard)
    lease_guard()
    holidays.prepare_global(lease_guard=lease_guard)


def run_tenant_stage(
    stage: dict,
    tenant_id: int,
    stage_run_id: str,
    data_as_of_utc: datetime,
    lease_guard: Callable[[], None],
    lease_fence: Callable[[object], None],
) -> TenantStageResult:
    module = importlib.import_module(stage["module"])
    kwargs = dict(
        tenant_id=tenant_id,
        run_id=stage_run_id,
        lease_guard=lease_guard,
        lease_fence=lease_fence,
    )
    if stage["stage"] in {"GRAPH", "TABULAR", "GNN"}:
        kwargs["data_as_of_utc"] = data_as_of_utc
    result = module.process_tenant(**kwargs)
    if not isinstance(result, TenantStageResult):
        raise TypeError(
            f"{stage['module']}.process_tenant returned {type(result).__name__}, "
            "expected TenantStageResult"
        )
    return result


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Weekly analytics job runner. Runs all stages by default; "
                    "use --only to run a single stage with the same harness "
                    "(DB wake-up, logging, exit codes).")
    ap.add_argument("--only", "--stage", dest="only", choices=_STAGE_KEYS, default=None,
                    metavar="STAGE",
                    help="run only this stage: " + ", ".join(_STAGE_KEYS))
    ap.add_argument("--tenant", dest="tenant", type=int, default=None,
                    metavar="TENANT_ID",
                    help="restrict all stages to a single tenant ID (local analysis only)")
    return ap.parse_args()


def _run_legacy(args: argparse.Namespace) -> bool:
    """Preserve the filtered stage harness for local analysis and recovery."""
    selected = [s for s in STAGES if args.only is None or s["key"] == args.only]
    tenants_to_process = [args.tenant] if args.tenant is not None else None

    results: dict[str, bool] = {}
    for stage in selected:
        ok = run_stage(
            name=f"{stage['label']}  ({stage['module']})",
            module_path=stage["module"],
            tenants_to_process=tenants_to_process,
        )
        results[stage["label"]] = ok
        if ok and stage["post"] is not None:
            stage["post"]()
    return all(results.values())


def _prepare_execution(
    coordinator: IntegrityAnalyticsCoordinator,
    run_id: str,
    worker_id: str,
    *,
    heartbeat_seconds: float,
    poll_seconds: float,
) -> bool:
    while True:
        state = coordinator.get_run_state(run_id)
        if state.preparation_status == "READY":
            return True
        if state.preparation_status == "FAILED" or state.result_status == "FAILED":
            return False

        if not coordinator.try_begin_preparation(run_id, worker_id):
            time.sleep(poll_seconds)
            continue

        heartbeat = LeaseHeartbeat(
            lambda: coordinator.heartbeat_preparation(run_id, worker_id),
            interval_seconds=heartbeat_seconds,
            label=f"preparation run={run_id} worker={worker_id}",
        )
        try:
            with heartbeat:
                run_global_preparation(heartbeat.assert_owned)
                heartbeat.assert_owned()
                source = connect_award_source()
                try:
                    tenant_ids = [tenant_id for tenant_id, _ in get_tenants(source)]
                finally:
                    source.close()
                queued = coordinator.initialize_tenant_queue(
                    run_id,
                    worker_id,
                    tenant_ids,
                )
                logger.info("GLOBAL PREPARATION queued %d enabled tenant(s)", queued)
            coordinator.mark_preparation_ready(run_id, worker_id)
            return True
        except LeaseLostError:
            logger.warning("Preparation lease was reclaimed; waiting for the new leader")
            time.sleep(poll_seconds)
        except Exception as exc:
            logger.error("GLOBAL PREPARATION failed: %s", exc, exc_info=True)
            try:
                coordinator.mark_preparation_failed(run_id, worker_id, str(exc))
                return False
            except LeaseLostError:
                logger.warning("Preparation failed after its lease was reclaimed")
                time.sleep(poll_seconds)


def _process_claim(
    coordinator: IntegrityAnalyticsCoordinator,
    claim,
    *,
    data_as_of_utc: datetime,
    heartbeat_seconds: float,
) -> None:
    run_id = claim.run_id
    tenant_id = claim.tenant_id
    worker_id = claim.worker_id
    logger.info(
        "TENANT CLAIM tenant=%d attempt=%d worker=%s reclaimed=%s",
        tenant_id,
        claim.attempt_count,
        worker_id,
        claim.reclaimed,
    )

    enabled_check = getattr(coordinator, "tenant_is_enabled", tenant_is_enabled)
    if not enabled_check(tenant_id):
        try:
            coordinator.mark_tenant_skipped_disabled(run_id, tenant_id, worker_id)
            logger.info("TENANT SKIPPED tenant=%d reason=disabled", tenant_id)
        except LeaseLostError:
            logger.warning("Disabled tenant %d was reclaimed before skip persisted", tenant_id)
        return

    heartbeat = LeaseHeartbeat(
        lambda: coordinator.heartbeat_tenant(run_id, tenant_id, worker_id),
        interval_seconds=heartbeat_seconds,
        label=f"tenant={tenant_id} run={run_id} worker={worker_id}",
    )
    failures: list[str] = []
    lease_lost = False
    with heartbeat:
        for stage in TENANT_STAGES:
            stage_run_id = _stage_run_id(run_id, tenant_id, stage["stage"])
            attempt = None
            try:
                heartbeat.assert_owned()
                attempt = coordinator.start_stage_attempt(
                    run_id,
                    tenant_id,
                    worker_id,
                    stage["stage"],
                    stage_run_id=stage_run_id,
                )
                logger.info(
                    "TENANT STAGE tenant=%d stage=%s attempt=%d",
                    tenant_id,
                    stage["stage"],
                    attempt.attempt_number,
                )
                outcome = run_tenant_stage(
                    stage,
                    tenant_id,
                    stage_run_id,
                    data_as_of_utc,
                    heartbeat.assert_owned,
                    lambda connection: coordinator.fence_tenant_lease(
                        connection,
                        run_id,
                        tenant_id,
                        worker_id,
                    ),
                )
                heartbeat.assert_owned()
                coordinator.finish_stage_attempt(
                    run_id,
                    tenant_id,
                    worker_id,
                    attempt.stage_attempt_id,
                    outcome.status,
                    reason_code=outcome.reason_code,
                    published_version=outcome.published_version,
                    diagnostics=outcome.diagnostics,
                )
            except LeaseLostError:
                lease_lost = True
                logger.error(
                    "TENANT LEASE LOST tenant=%d stage=%s; publication stopped",
                    tenant_id,
                    stage["stage"],
                )
                break
            except Exception as exc:
                failures.append(f"{stage['stage']}: {exc}")
                logger.error(
                    "TENANT STAGE FAILED tenant=%d stage=%s: %s",
                    tenant_id,
                    stage["stage"],
                    exc,
                    exc_info=True,
                )
                if attempt is not None:
                    try:
                        heartbeat.assert_owned()
                        coordinator.finish_stage_attempt(
                            run_id,
                            tenant_id,
                            worker_id,
                            attempt.stage_attempt_id,
                            "FAILED",
                            reason_code=f"{stage['stage']}_FAILED",
                            failure_detail=str(exc),
                        )
                    except LeaseLostError:
                        lease_lost = True
                        break

    if lease_lost:
        return
    try:
        coordinator.complete_tenant(
            run_id,
            tenant_id,
            worker_id,
            "FAILED" if failures else "SUCCEEDED",
            failure_detail="; ".join(failures) if failures else None,
        )
    except LeaseLostError:
        logger.warning("Tenant %d was reclaimed before its result persisted", tenant_id)


def _run_claim_loop(
    coordinator: IntegrityAnalyticsCoordinator,
    run_id: str,
    worker_id: str,
    *,
    data_as_of_utc: datetime,
    heartbeat_seconds: float,
    poll_seconds: float,
) -> str:
    while True:
        result = coordinator.get_result(run_id)
        if result is not None:
            return result

        claim = coordinator.try_claim_tenant(run_id, worker_id)
        if claim is not None:
            _process_claim(
                coordinator,
                claim,
                data_as_of_utc=data_as_of_utc,
                heartbeat_seconds=heartbeat_seconds,
            )
            continue

        state = coordinator.get_run_state(run_id)
        if state.result_status != "RUNNING":
            return state.result_status
        if state.pending_tenants or state.running_tenants:
            time.sleep(poll_seconds)
            continue
        if not state.barrier_ready:
            time.sleep(poll_seconds)
            continue

        if coordinator.try_begin_finalization(run_id, worker_id):
            summary = coordinator.build_run_summary(run_id)
            if summary["tabular_model_published"]:
                notify_api_refresh()
            final_result = "FAILED" if summary["failed_tenants"] else "SUCCEEDED"
            coordinator.complete_finalization(
                run_id,
                worker_id,
                final_result,
                summary=summary,
                failure_detail=(
                    f"{summary['failed_tenants']} tenant(s) failed"
                    if summary["failed_tenants"] else None
                ),
            )
            return final_result
        time.sleep(poll_seconds)


def run_coordinated_job(
    *,
    execution_name: str,
    job_name: str,
    coordinator: IntegrityAnalyticsCoordinator,
    worker_id: str,
    heartbeat_seconds: float,
    poll_seconds: float,
) -> str:
    run = coordinator.register_execution(execution_name, job_name)
    logger.info(
        "COORDINATED RUN run=%s execution=%s worker=%s data_as_of_utc=%s",
        run.run_id,
        execution_name,
        worker_id,
        run.data_as_of_utc,
    )
    if not _prepare_execution(
        coordinator,
        run.run_id,
        worker_id,
        heartbeat_seconds=heartbeat_seconds,
        poll_seconds=poll_seconds,
    ):
        return coordinator.get_result(run.run_id) or "FAILED"
    return _run_claim_loop(
        coordinator,
        run.run_id,
        worker_id,
        data_as_of_utc=run.data_as_of_utc,
        heartbeat_seconds=heartbeat_seconds,
        poll_seconds=poll_seconds,
    )


def main() -> None:
    args = _parse_args()

    logger.info("WEEKLY ANALYTICS JOB - START")
    logger.info("Environment : %s", os.getenv("ENVIRONMENT", "unknown"))
    logger.info("Award SQL   : %s / %s", os.getenv("AWARD_SQL_SERVER", "(not set)"), os.getenv("AWARD_SQL_DATABASE", "(not set)"))
    logger.info("Sentinel SQL: %s / %s", os.getenv("IS_SQL_SERVER", "(not set)"), os.getenv("IS_SQL_DATABASE", "(not set)"))
    logger.info("Storage acct: %s", os.getenv("AZURE_STORAGE_ACCOUNT", "(not set)"))
    logger.info("Stages      : %s", args.only or "ALL (%s)" % ", ".join(_STAGE_KEYS))
    logger.info("Tenant      : %s", args.tenant or "ALL")

    # ── DB wake-up — must succeed before any stage runs ──────────────────────
    # Serverless SQL auto-pauses after 60 min; resuming takes 60–90 s. Every
    # stage needs the DB, so we wake it up regardless of which stage(s) we run.
    try:
        wake_database()
    except RuntimeError as exc:
        logger.error("Cannot proceed — database is unreachable: %s", exc)
        sys.exit(1)

    if args.only is not None or args.tenant is not None:
        passed = _run_legacy(args)
        logger.info("FILTERED ANALYTICS RUN result=%s", "SUCCEEDED" if passed else "FAILED")
        sys.exit(0 if passed else 1)

    lease_seconds = int(os.getenv("COORDINATION_LEASE_SECONDS", "300"))
    heartbeat_seconds = float(os.getenv("COORDINATION_HEARTBEAT_SECONDS", "60"))
    poll_seconds = float(os.getenv("COORDINATION_POLL_SECONDS", "5"))
    if heartbeat_seconds >= lease_seconds:
        raise RuntimeError("COORDINATION_HEARTBEAT_SECONDS must be below the lease duration")

    environment = os.getenv("ENVIRONMENT", "unknown")
    execution_name = os.getenv("CONTAINER_APP_JOB_EXECUTION_NAME")
    if not execution_name:
        if environment not in {"unknown", "local", "test"}:
            raise RuntimeError("CONTAINER_APP_JOB_EXECUTION_NAME is required in Azure")
        execution_name = f"local-{uuid.uuid4()}"
    job_name = (
        os.getenv("CONTAINER_APP_JOB_NAME")
        or os.getenv("OTEL_SERVICE_NAME")
        or "fraud-analytics-job"
    )
    coordinator = IntegrityAnalyticsCoordinator(lease_seconds=lease_seconds)
    result = run_coordinated_job(
        execution_name=execution_name,
        job_name=job_name,
        coordinator=coordinator,
        worker_id=new_worker_id(),
        heartbeat_seconds=heartbeat_seconds,
        poll_seconds=poll_seconds,
    )
    logger.info("INTEGRITY ANALYTICS JOB - SUMMARY result=%s", result)
    sys.exit(0 if result == "SUCCEEDED" else 1)


if __name__ == "__main__":
    main()
