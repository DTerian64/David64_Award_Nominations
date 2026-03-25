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


def post_fork(server, worker):
    """Initialize per-worker resources after Gunicorn forks each worker process.

    This is the correct place to call configure_azure_monitor() when running
    under Gunicorn's pre-fork model. Each worker gets a fresh OTel setup with
    its own background exporter threads.
    """
    conn_str = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if conn_str:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor
            configure_azure_monitor(
                connection_string=conn_str,
                # Disable FastAPI/ASGI auto-instrumentation — it wraps the ASGI
                # callable outside Starlette's middleware chain and can interfere
                # with CORSMiddleware. All other instrumentors remain active:
                # httpx (outbound calls), sqlalchemy (DB queries), logging, exceptions.
                instrumentation_options={"fastapi": {"enabled": False}},
            )
            logger.info(
                "[worker pid=%s] Azure Monitor OpenTelemetry configured "
                "(FastAPI instrumentation disabled).",
                worker.pid,
            )
        except Exception as exc:  # noqa: BLE001
            # Never let observability bootstrap crash the application.
            logger.warning(
                "[worker pid=%s] Azure Monitor OpenTelemetry failed to configure: %s",
                worker.pid,
                exc,
            )
    else:
        logger.warning(
            "[worker pid=%s] APPLICATIONINSIGHTS_CONNECTION_STRING not set — "
            "Azure Monitor disabled.",
            worker.pid,
        )
