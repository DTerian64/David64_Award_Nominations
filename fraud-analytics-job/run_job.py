"""
run_job.py — Fraud Analytics Job entrypoint
============================================
Orchestrates the weekly analytics pipeline in dependency order. The registry
below (STAGES) is the single source of truth; this list documents it.

  Stage 1: modeling/graph_analytics.py
      Syncs the Azure SQL Graph tables (NomGraph_Person, NomGraph_Nominated).
      Runs ring, dense-block, temporal-burst, super-nominator,
      super-beneficiary, nomination-desert, and hidden-candidate analysis.
      Runs sentence-transformers for copy-paste analysis.
      Upserts findings into dbo.GraphPatternFindings.
      Materialises per-user graph flag snapshots into dbo.UserGraphFlags
      for nomination-time graph analysis and audit use cases.

  Stage 2: modeling/train_rf_model.py
      Per-tenant Random Forest retrain on Nominations and the shared human-label contract.
      Uses nomination, relationship, amount, category, and semantic-description
      features only; Graph Analytics findings remain an independent opinion.
      Uploads the retrained .pkl model to Azure Blob Storage.

  Stage 3: modeling/train_gnn_model.py
      Per-tenant heterogeneous GNN — HeteroConv + SAGEConv over user and
      nomination nodes, trained on a three-window temporal split so message
      passing, training targets and evaluation targets never overlap.

      Split encoder/decoder by design: the encoder runs HERE and persists one
      embedding per user to dbo.GNN_UserEmbeddings; only the ~15k-parameter
      decoder head ships to integrity-check. That is what keeps PyTorch
      Geometric out of the inference image.

      Whenever a trained artifact and matching embeddings are available,
      integrity-check includes the GNN opinion in nomination routing.

      Reads graph topology straight from dbo.Nominations / dbo.Users, NOT from
      dbo.UserGraphFlags. That independence is the point: a GNN fed the Random
      Forest's engineered graph features would just be relearning the detector
      it is meant to be a second opinion on.

      Skips a tenant rather than failing it when below GNN_MIN_TRAINING_SAMPLES
      / GNN_MIN_USERS / GNN_MIN_POSITIVES. Those gates are empirical: in the
      The synthetic ablation found that a 50-user tenant scored WORSE with message passing than
      without it, so training a small tenant is not a neutral act.

      Requires the tables from Alembic revision 0040. Set GNN_ENABLED=false to
      skip the stage without rebuilding the image.

  Stage 4: misc_jobs/sync_holidays.py
      Refreshes dbo.Holidays from the Nager.Date API, falling back to the
      offline `holidays` library per country/year, so Stage 5's is_holiday
      calendar feature stays correct per tenant locale.

  Stage 5: modeling/forecast_models.py
      Per-tenant bake-off (Seasonal-Naive vs ETS vs LightGBM, ranked by
      rolling-origin MASE) writing to dbo.ForecastRuns / dbo.Forecasts.

  ORDERING
    Stage 1 and Stage 2 are model-independent. They remain sequenced for stable
      operations, but the RF does not consume Graph Analytics findings.
    Stage 2 before Stage 3 — stable operational ordering only. Both models read
      human outcomes from dbo.IntegrityDecisionResults and remain independent.
    Stage 4 before Stage 5 — the forecast reads the holiday calendar.

  ISOLATION
    run_stage() catches per-stage exceptions, so one failing stage does not stop
    the others; the job still exits 1 at the end. A GNN failure can therefore
    never block the Random Forest retrain.

Exit codes:
  0  — all stages succeeded
  1  — one or more stages failed (Container Apps Job reports execution failure;
       Azure Monitor alert rule fires on non-zero exit)

Logging:
  Structured stdout — picked up by the Container Apps Environment log stream
  and forwarded to the Log Analytics workspace defined in the CAE.
"""

import argparse
import logging
import os
import sys
import time
import urllib.request
import urllib.error
import json
from pathlib import Path

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
# (fraud-analytics-job/). Modeling stages live in the modeling package; shared
# infrastructure lives in utils. The full job directory is copied into the
# image, so dotted package imports work without cross-directory COPY steps.
JOB_DIR = Path(__file__).parent.resolve()   # /app  (same dir as this file)
sys.path.insert(0, str(JOB_DIR))

# Stage scripts are invoked as modules so they share the same process and
# benefit from any cached state (DB connection pool, loaded model, etc.).
# Each script's __main__ guard is bypassed — we call their main() directly.


def wake_database(
    max_attempts: int = 8,
    attempt_timeout_s: int = 120,
    retry_delay_s: float = 20.0,
) -> None:
    """
    Ensure the Azure SQL Serverless database is awake before running any stage.

    The database auto-pauses after 60 minutes of inactivity. This job fires at
    2 AM UTC Monday, so the DB is almost always paused on arrival. Resuming a
    serverless database takes 60–90 seconds. We poll with a lightweight
    SELECT 1 query until it responds, logging progress at each attempt.

    Raises RuntimeError if the database cannot be reached after all attempts.
    Total wait budget: max_attempts × (attempt_timeout_s + retry_delay_s)
                     = 8 × (120 + 20) = ~18 minutes
    """
    server   = os.getenv("SQL_SERVER", "(not set)")
    database = os.getenv("SQL_DATABASE", "(not set)")
    from utils.db_conn import connect  # Managed Identity token auth

    logger.info("DB WAKE-UP  server=%s  database=%s", server, database)
    logger.info("  Serverless auto-pause means the DB may be cold.")
    logger.info("  Will poll up to %d times (timeout %ds each).", max_attempts, attempt_timeout_s)

    t_start = time.monotonic()
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        t_attempt = time.monotonic()
        logger.info("DB WAKE-UP  attempt %d/%d — connecting...", attempt, max_attempts)
        try:
            conn = connect(attempt_timeout_s)
            conn.execute("SELECT 1").fetchone()
            conn.close()
            elapsed = time.monotonic() - t_start
            logger.info(
                "DB WAKE-UP  ✓ database is awake  (total wait: %.1f s, attempts: %d)",
                elapsed, attempt,
            )
            return
        except Exception as exc:
            last_exc = exc
            elapsed_attempt = time.monotonic() - t_attempt
            elapsed_total   = time.monotonic() - t_start
            logger.warning(
                "DB WAKE-UP  attempt %d/%d failed after %.1f s (total elapsed: %.1f s): %s",
                attempt, max_attempts, elapsed_attempt, elapsed_total, exc,
            )
            if attempt < max_attempts:
                logger.info("DB WAKE-UP  waiting %.0f s before next attempt...", retry_delay_s)
                time.sleep(retry_delay_s)

    elapsed = time.monotonic() - t_start
    logger.error(
        "DB WAKE-UP  ✗ database did not respond after %d attempts (%.1f s total).",
        max_attempts, elapsed,
    )
    raise RuntimeError(
        f"SQL database did not wake up after {max_attempts} attempts ({elapsed:.0f}s). "
        f"Last error: {last_exc}"
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


# ── Stage registry ───────────────────────────────────────────────────────────
# Single source of truth. `key` is what --only accepts; `module` is its import path.
# `post` is an optional hook run only if the stage succeeded.
STAGES = [
    {"key": "graph_analytics", "label": "Graph Analytics",   "module": "modeling.graph_analytics", "post": None},
    {"key": "train_rf_model",  "label": "RF model training", "module": "modeling.train_rf_model",  "post": notify_api_refresh},
    # GNN training. Placed after train_rf_model for two reasons: it
    # consumes the label view the RF has just refreshed, and a GNN failure can then
    # never block the RF retrain — run_stage()'s per-stage try/except gives that
    # isolation for free. No post-hook: the backend does not consume the GNN, so
    # /api/internal/refresh-fraud-model is irrelevant to it; integrity-check streams
    # the decoder itself on first use per tenant.
    {"key": "train_gnn_model",        "label": "GNN model training",       "module": "modeling.train_gnn_model", "post": None},
    {"key": "sync_holidays",          "label": "Holiday sync",             "module": "misc_jobs.sync_holidays", "post": None},
    {"key": "forecast_models",        "label": "Forecast models",          "module": "modeling.forecast_models", "post": None},
]
_STAGE_KEYS = [s["key"] for s in STAGES]


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


def main() -> None:
    args = _parse_args()
    selected = [s for s in STAGES if args.only is None or s["key"] == args.only]
    tenants_to_process = [args.tenant] if args.tenant is not None else None

    logger.info("WEEKLY ANALYTICS JOB - START")
    logger.info("Environment : %s", os.getenv("ENVIRONMENT", "unknown"))
    logger.info("SQL Server  : %s", os.getenv("SQL_SERVER", "(not set)"))
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

    results: dict[str, bool] = {}
    for stage in selected:
        ok = run_stage(
            name                = f"{stage['label']}  ({stage['module']})",
            module_path         = stage["module"],
            tenants_to_process  = tenants_to_process,
        )
        results[stage["label"]] = ok
        # Stage-specific post-hook, only on success.
        if ok and stage["post"] is not None:
            stage["post"]()

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info("FRAUD ANALYTICS JOB - SUMMARY")
    all_passed = True
    for stage, passed in results.items():
        status = "✓  PASS" if passed else "✗  FAIL"
        logger.info("  %s  %s", status, stage)
        if not passed:
            all_passed = False

    if all_passed:
        logger.info("")
        logger.info("All stages completed successfully.")
        sys.exit(0)
    else:
        logger.error("")
        logger.error("One or more stages failed — see logs above for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
