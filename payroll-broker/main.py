# Payroll Broker — FastAPI Application
# ======================================
# Architecture: FastAPI + Azure SQL + Azure Service Bus + pluggable payroll providers
#
# Two responsibilities running in one process:
#   1. HTTP server  — provider OAuth & webhook endpoints + /health
#   2. Background worker — consumes nomination.approved from Service Bus
#                          and dispatches payroll submissions via PROVIDER_REGISTRY
#
# Adding a new provider
# ---------------------
# 1. Implement providers/<name>/ (client.py, provider.py, routers)
# 2. Register in providers/registry.py
# 3. Include the provider's routers below

import logging
from logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

import asyncio
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from workers.payroll_worker import run_worker

# ── Routers ───────────────────────────────────────────────────────────────────
from routers.health_router import router as health_router

# Gusto
from providers.gusto.oauth_router   import router as gusto_oauth_router
from providers.gusto.webhook_router import router as gusto_webhook_router

# Workday (uncomment when providers/workday/ is implemented)
# from providers.workday.oauth_router   import router as workday_oauth_router
# from providers.workday.webhook_router import router as workday_webhook_router


# ============================================================================
# LIFESPAN — startup / shutdown
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: configure telemetry, start the SB worker."""

    # Azure Monitor / OpenTelemetry
    _ai_conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if _ai_conn:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor
            configure_azure_monitor(
                connection_string=_ai_conn,
                instrumentation_options={"fastapi": {"enabled": False}},
            )
            logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
            logging.getLogger("azure.monitor.opentelemetry.exporter").setLevel(logging.WARNING)
            logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
            logger.debug("Azure Monitor configured (pid=%d)", os.getpid())
        except Exception as exc:
            logger.warning("Azure Monitor configuration failed: %s", exc)
    else:
        logger.warning("APPLICATIONINSIGHTS_CONNECTION_STRING not set — telemetry disabled")

    # DB schema is managed exclusively by backend/alembic — no create_all here.

    # Start the Service Bus consumer worker
    stop_event  = asyncio.Event()
    worker_task = asyncio.create_task(run_worker(stop_event))
    logger.info("Payroll worker task started.")

    yield

    # Shutdown: signal the worker loop and wait for clean exit
    stop_event.set()
    try:
        await asyncio.wait_for(worker_task, timeout=15)
    except asyncio.TimeoutError:
        logger.warning("Payroll worker did not stop within 15s — cancelling")
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    logger.info("Payroll worker stopped.")


# ============================================================================
# FASTAPI APPLICATION
# ============================================================================

app = FastAPI(
    lifespan=lifespan,
    title="Payroll Broker",
    description=(
        "Intermediary service between the Award Nomination system and external "
        "payroll providers.  Handles provider OAuth onboarding and off-cycle "
        "bonus payroll submission triggered by nomination approvals via "
        "Azure Service Bus."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
FastAPIInstrumentor.instrument_app(app)

# ── Register routers ──────────────────────────────────────────────────────────
app.include_router(health_router)
app.include_router(gusto_oauth_router)
app.include_router(gusto_webhook_router)
# app.include_router(workday_oauth_router)
# app.include_router(workday_webhook_router)


# ============================================================================
# ROOT
# ============================================================================

@app.get("/")
async def root():
    return {"status": "healthy", "service": "Payroll Broker"}


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)
