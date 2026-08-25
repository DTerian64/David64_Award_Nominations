# Award Nomination System - FastAPI Application
# Architecture: FastAPI + Azure SQL + Entra ID + Email Notifications

import logging
from logging_config import setup_logging

# Set up logging at the top of the file
setup_logging()
logger = logging.getLogger(__name__)

import socket
from dotenv import load_dotenv
load_dotenv()

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Import authentication functions from auth.py
from auth import require_role

import utils.sqlhelper2 as sqlhelper  # Database helper functions for Azure SQL
from utils.rf_model_cache import rf_model_cache

# ── Routers ───────────────────────────────────────────────────────────────────
from routers.demo_router        import router as demo_router
from routers.hrbp_router        import router as hrbp_router
from routers.users_router       import router as users_router
from routers.nominations_router import router as nominations_router
from routers.admin_router       import router as admin_router
from routers.analytics_router   import router as analytics_router
from routers.webhooks_router    import router as webhooks_router
from routers.internal_router    import router as internal_router
from routers.payroll_router     import router as payroll_router
from routers.setup_router       import router as setup_router

# ============================================================================
# CONFIGURATION
# ============================================================================

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")

# ============================================================================
# FASTAPI APPLICATION
# ============================================================================

from fastapi.openapi.docs import get_swagger_ui_html
from routers.schemas import HealthResponse


async def _model_eviction_loop() -> None:
    """
    Background task: periodically evict idle tenant fraud models from memory.

    Interval is controlled by MODEL_EVICTION_INTERVAL_SECONDS (default 300 = 5 min).
    Models that have not been used within MODEL_IDLE_TTL_SECONDS (default 1800 = 30 min)
    are dropped from the in-process cache; they will be lazy-reloaded on the
    next request for that tenant.
    """
    import asyncio
    interval = int(os.getenv('MODEL_EVICTION_INTERVAL_SECONDS', '300'))
    logger.info("Model eviction loop starting — interval=%ds.", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            n = rf_model_cache.evict_idle()
            if n:
                logger.info("Model eviction: removed %d idle tenant model(s).", n)
        except Exception as exc:
            logger.error("Model eviction error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler – runs startup logic, then yields control."""
    import asyncio

    # ── Azure Monitor / OpenTelemetry ─────────────────────────────────────────
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
            logger.debug("Azure Monitor OpenTelemetry configured (pid=%d).", os.getpid())
        except Exception as exc:
            logger.warning("Azure Monitor OpenTelemetry failed to configure: %s", exc)
    else:
        logger.warning("APPLICATIONINSIGHTS_CONNECTION_STRING not set — telemetry disabled.")

    # Drop uvicorn's own access-log lines for /health — Front Door probes this
    # path every ~30s per origin (terraform/modules/front-door), and its
    # multi-PoP architecture can multiply that well beyond the nominal
    # interval. Request-level telemetry for /health is already captured via
    # FastAPIInstrumentor -> AppRequests (sampled via OTEL_TRACES_SAMPLER_ARG),
    # so nothing is lost by keeping it out of stdout / ContainerAppConsoleLogs.
    # Must be attached here (lifespan startup), not in logging_config.py —
    # UvicornWorker runs its own dictConfig() after post_fork() and before
    # lifespan startup, which would wipe a filter attached any earlier.
    class _HealthCheckLogFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "/health" not in record.getMessage()

    logging.getLogger("uvicorn.access").addFilter(_HealthCheckLogFilter())

    # Schema is owned by the standalone `schema-migration` project (Alembic) and
    # applied by its pipeline (ADR-0001). The backend does not create/alter tables
    # at startup — its runtime identity holds no DDL rights.

    # Start background task that evicts idle per-tenant fraud models
    eviction_task = asyncio.create_task(_model_eviction_loop())
    logger.info("Fraud model eviction loop started.")

    yield

    # Shutdown: cancel the eviction loop cleanly
    eviction_task.cancel()
    try:
        await eviction_task
    except asyncio.CancelledError:
        pass
    logger.info("Fraud model eviction loop stopped.")


app = FastAPI(
    lifespan=lifespan,
    title="Award Nomination System",
    description="Employee recognition and monetary award nomination system",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)

# Instrument FastAPI for HTTP request tracing
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
FastAPIInstrumentor.instrument_app(app)

# ============================================================================
# CORS CONFIGURATION — must be added immediately after app creation, before routes
# ============================================================================

_cors_env = os.getenv("CORS_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()]
logger.info("CORS allowed origins: %s", ALLOWED_ORIGINS)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "X-Requested-With",
        "X-Impersonate-User",
        "traceparent",
        "tracestate",
        "baggage",
        "Request-Id",
        "Correlation-Context",
    ],
    expose_headers=["*"],
    max_age=3600,
)

# ── Validation error handler ─────────────────────────────────────────────────
# FastAPI's default 422 response wraps Pydantic errors as an array:
#   { "detail": [{ "loc": [...], "msg": "...", "type": "..." }, ...] }
# The frontend's apiCall() does `errorData.detail.toString()` which turns an
# array into "[object Object]".  This handler flattens it to a plain string so
# every error response has a consistent `{ "detail": "<string>" }` shape.

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    if errors:
        # Prefer the human-readable 'msg' from the first failing field.
        # e.g. "String should have at least 10 characters"
        msg = errors[0].get("msg", "Invalid request")
        # Strip the Pydantic "Value error, " prefix when present
        if msg.lower().startswith("value error, "):
            msg = msg[len("value error, "):]
    else:
        msg = "Invalid request"
    return JSONResponse(status_code=422, content={"detail": msg})


# ── Register routers ──────────────────────────────────────────────────────────
app.include_router(demo_router)
app.include_router(hrbp_router)
app.include_router(users_router)
app.include_router(nominations_router)
app.include_router(admin_router)
app.include_router(analytics_router)
app.include_router(webhooks_router)
app.include_router(internal_router)
app.include_router(payroll_router)
app.include_router(setup_router)

# ============================================================================
# INFRASTRUCTURE ENDPOINTS
# ============================================================================

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
        swagger_ui_parameters={"persistAuthorization": True},
        init_oauth={
            "clientId": CLIENT_ID,
            "scopes": f"api://{CLIENT_ID}/access_as_user openid profile email",
        }
    )


@app.get(app.swagger_ui_oauth2_redirect_url or "/oauth2-redirect", include_in_schema=False)
async def swagger_ui_redirect():
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""
    <!doctype html>
    <html lang="en-US">
    <head>
        <title>Swagger UI: OAuth2 Redirect</title>
    </head>
    <body>
    <script>
        'use strict';
        function run () {
            var oauth2 = window.opener.swaggerUIRedirectOauth2;
            var sentState = oauth2.state;
            var redirectUrl = oauth2.redirectUrl;
            var isValid, qp, arr;

            if (/code|token|error/.test(window.location.hash)) {
                qp = window.location.hash.substring(1).replace('?', '&');
            } else {
                qp = location.search.substring(1);
            }

            arr = qp.split("&");
            arr.forEach(function (v,i,_arr) { _arr[i] = '"' + v.replace('=', '":"') + '"';});
            qp = qp ? JSON.parse('{' + arr.join() + '}',
                    function (key, value) {
                        return key === "" ? value : decodeURIComponent(value);
                    }
            ) : {};

            isValid = qp.state === sentState;

            if ((
              oauth2.auth.schema.get("flow") === "accessCode" ||
              oauth2.auth.schema.get("flow") === "authorizationCode" ||
              oauth2.auth.schema.get("flow") === "authorization_code"
            ) && !oauth2.auth.code) {
                if (!isValid) {
                    oauth2.errCb({
                        authId: oauth2.auth.name,
                        source: "auth",
                        level: "warning",
                        message: "Authorization may be unsafe, passed state was changed in server. The passed state wasn't returned from auth server."
                    });
                }

                if (qp.code) {
                    delete oauth2.state;
                    oauth2.auth.code = qp.code;
                    oauth2.callback({auth: oauth2.auth, redirectUrl: redirectUrl});
                } else {
                    let oauthErrorMsg;
                    if (qp.error) {
                        oauthErrorMsg = "["+qp.error+"]: " +
                            (qp.error_description ? qp.error_description+ ". " : "no accessCode received from the server. ") +
                            (qp.error_uri ? "More info: "+qp.error_uri : "");
                    }

                    oauth2.errCb({
                        authId: oauth2.auth.name,
                        source: "auth",
                        level: "error",
                        message: oauthErrorMsg || "[Authorization failed]: no accessCode received from the server."
                    });
                }
            } else {
                oauth2.callback({auth: oauth2.auth, token: qp, isValid: isValid, redirectUrl: redirectUrl});
            }
            window.close();
        }

        if (document.readyState !== 'loading') {
            run();
        } else {
            document.addEventListener('DOMContentLoaded', function () {
                run();
            });
        }
    </script>
    </body>
    </html>
    """)


@app.get("/")
async def root():
    """Health check endpoint"""
    return {"status": "healthy", "service": "Award Nomination System"}


@app.get("/whoami")
def whoami(_claims=Depends(require_role("AWard_Nomination_Admin"))):
    """Diagnostic endpoint for Azure Front Door routing (AWard_Nomination_Admin only)"""
    return {
        "region":         os.getenv("REGION", "unknown"),
        "container_app":  os.getenv("CONTAINER_APP_NAME", "unknown"),
        "revision":       os.getenv("CONTAINER_APP_REVISION", "unknown"),
        "hostname":       socket.gethostname(),
    }


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok")


@app.head("/health")
def health_head():
    return Response(status_code=200)


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)
