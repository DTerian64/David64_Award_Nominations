"""
run_job.py — Fraud Analytics Job entrypoint
============================================
Orchestrates the two-stage weekly fraud analytics pipeline:

  Stage 1: train_fraud_model.py
      Per-tenant Random Forest retrain on the Nominations + FraudScores tables.
      Upserts updated fraud scores into dbo.FraudScores.
      Uploads the retrained .pkl model to Azure Blob Storage.

  Stage 2: graph_pattern_detector.py
      Syncs the Azure SQL Graph tables (NomGraph_Person, NomGraph_Nominated).
      Runs MATCH queries for ring detection and approver affinity.
      Runs networkx analysis for super-nominators and nomination deserts.
      Runs sentence-transformers for copy-paste and transactional language.
      Upserts findings into dbo.GraphPatternFindings.

Exit codes:
  0  — both stages succeeded
  1  — one or both stages failed (Container Apps Job reports execution failure;
       Azure Monitor alert rule fires on non-zero exit)

Logging:
  Structured stdout — picked up by the Container Apps Environment log stream
  and forwarded to the Log Analytics workspace defined in the CAE.
"""

import logging
import os
import sys
import time
from pathlib import Path

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=os.getenv("LOGGING_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("fraud_analytics_job")

# ── Path setup ───────────────────────────────────────────────────────────────
# WORKDIR in the container is /app, which is also the build context
# (analytics/fraud-analytics-job/).  All pipeline scripts live in the same
# directory, so they are importable as flat top-level modules — no dotted
# package paths, no cross-directory COPY gymnastics in the Dockerfile.
JOB_DIR = Path(__file__).parent.resolve()   # /app  (same dir as this file)
sys.path.insert(0, str(JOB_DIR))

# Stage scripts are invoked as modules so they share the same process and
# benefit from any cached state (DB connection pool, loaded model, etc.).
# Each script's __main__ guard is bypassed — we call their main() directly.


def run_stage(name: str, module_path: str) -> bool:
    """
    Import and execute the main() function of a pipeline stage.
    Returns True on success, False on any exception.
    """
    logger.info("=" * 60)
    logger.info("STAGE: %s", name)
    logger.info("=" * 60)
    t0 = time.monotonic()
    try:
        import importlib
        mod = importlib.import_module(module_path)
        mod.main()
        elapsed = time.monotonic() - t0
        logger.info("✓  %s completed in %.1f s", name, elapsed)
        return True
    except Exception as exc:
        elapsed = time.monotonic() - t0
        logger.error("✗  %s FAILED after %.1f s: %s", name, elapsed, exc, exc_info=True)
        return False


def main() -> None:
    logger.info("╔══════════════════════════════════════════════════╗")
    logger.info("║        FRAUD ANALYTICS JOB — START               ║")
    logger.info("╚══════════════════════════════════════════════════╝")
    logger.info("Environment : %s", os.getenv("ENVIRONMENT", "unknown"))
    logger.info("SQL Server  : %s", os.getenv("SQL_SERVER", "(not set)"))
    logger.info("Storage acct: %s", os.getenv("AZURE_STORAGE_ACCOUNT", "(not set)"))

    results: dict[str, bool] = {}

    # ── Stage 1: Random Forest retrain ───────────────────────────────────────
    results["RF model training"] = run_stage(
        name        = "RF model training  (train_fraud_model)",
        module_path = "train_fraud_model",
    )

    # ── Stage 2: Graph pattern detection ─────────────────────────────────────
    # Runs regardless of Stage 1 outcome — graph findings are independent.
    results["Graph pattern detection"] = run_stage(
        name        = "Graph pattern detection  (graph_pattern_detector)",
        module_path = "graph_pattern_detector",
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info("")
    logger.info("╔══════════════════════════════════════════════════╗")
    logger.info("║        FRAUD ANALYTICS JOB — SUMMARY             ║")
    logger.info("╚══════════════════════════════════════════════════╝")
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
