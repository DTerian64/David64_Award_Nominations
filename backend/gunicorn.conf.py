# gunicorn.conf.py
# ─────────────────────────────────────────────────────────────────────────────
# Gunicorn configuration — auto-discovered from WORKDIR (/app) at startup.
#
# OpenTelemetry + Gunicorn pre-fork model
# ───────────────────────────────────────
# configure_azure_monitor() MUST be called per-worker (here in post_fork),
# NOT in main.py (the master process).
#
# Why: Gunicorn loads main.py in the master, then os.fork()s workers.
# Calling configure_azure_monitor() in main.py means:
#   1. OTel background exporter threads start in the master — they do NOT
#      survive the fork into child workers (threads are not copied on fork).
#   2. Workers inherit a half-initialized OTel global state (tracers, exporters,
#      meter provider) pointing to dead thread state.
#   3. This can corrupt the ASGI middleware stack order and cause Starlette's
#      CORSMiddleware to return 400 on OPTIONS preflight requests.
#
# post_fork() runs in each worker after forking, giving each worker a clean
# process with no inherited OTel thread state. configure_azure_monitor() then
# initializes OTel correctly — fresh exporters, fresh background threads.
#
# FastAPI/ASGI auto-instrumentation is kept disabled (instrumentation_options)
# because it wraps the ASGI callable and can affect middleware ordering.
# Logging, httpx, sqlalchemy, and exception tracking remain fully active.
# ─────────────────────────────────────────────────────────────────────────────

import logging
import os

logger = logging.getLogger(__name__)


def on_starting(server):
    """
    Called once in the Gunicorn master process before any workers are forked.

    We load the sentence-transformers model here so the ~500 MB PyTorch weights
    land in the master's address space.  When workers fork they inherit the
    model's memory pages as read-only CoW mappings — the OS never needs to copy
    them as long as the workers only read (which they do — inference is
    stateless).  Net result: one copy of the model shared across all workers
    instead of N independent copies loaded on first request.

    This has no interaction with OTel/Azure Monitor — configure_azure_monitor()
    is still called per-worker inside the FastAPI lifespan (main.py), after
    uvicorn has finished configuring logging.
    """
    model_name = os.getenv("EMBED_MODEL_NAME", "all-MiniLM-L6-v2")
    logger.info("on_starting: pre-loading sentence-transformer '%s' in master …", model_name)
    try:
        from fraud_ml import _get_embed_model
        _get_embed_model(model_name)
        logger.info("on_starting: model loaded — workers will share via CoW.")
    except Exception as exc:
        # Non-fatal: workers will lazy-load individually on first request.
        logger.warning("on_starting: model pre-load failed (%s) — workers will load on demand.", exc)


def post_fork(server, worker):
    """Called in each worker immediately after forking from the master.

    Azure Monitor / OTel is intentionally NOT configured here.

    UvicornWorker calls logging.config.dictConfig() during its own startup,
    which runs after post_fork() and wipes any handlers added here.  OTel is
    therefore initialized in the FastAPI lifespan startup event (main.py),
    which runs after uvicorn has finished configuring logging.
    """
    pass
