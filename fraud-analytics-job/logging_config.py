"""
logging_config.py — structured logging for the weekly analytics job
===================================================================
Mirrors backend/logging_config.py so the job's logs behave identically in
Log Analytics. Records that originate from the job's own code (this directory)
get an ``App_Log: `` prefix and are emitted as JSON, so they can be isolated in
ContainerAppConsoleLogs with:

    | where message startswith "App_Log:"

Third-party records (lightgbm, statsmodels, pyodbc, azure-sdk, sentence-
transformers, etc.) are dropped at the handler level so they don't clutter the
stream — matching the backend's behaviour. (If you'd rather keep library
WARN/ERROR lines, relax _AppLogFilter.filter to also return True for
record.levelno >= logging.WARNING.)
"""
import json
import logging
import os
import sys
from datetime import datetime

# Absolute path of this job's directory. Records whose pathname falls outside
# this dir are treated as third-party. All stage modules (run_job,
# forecast_models, train_fraud_model, graph_pattern_detector) live here.
_APP_DIR = os.path.abspath(os.path.dirname(__file__))

_STANDARD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
})


class _AppLogFilter(logging.Filter):
    """Pass only records from the job's own code, prefixing them 'App_Log: '."""

    def filter(self, record: logging.LogRecord) -> bool:
        if os.path.abspath(record.pathname).startswith(_APP_DIR):
            record.msg = f"App_Log: {record.msg}"
            return True
        return False  # drop — not our code


class _ExtrasToMessageFilter(logging.Filter):
    """Merge extra={} kwargs into the message body as JSON (App Insights-safe)."""

    def filter(self, record: logging.LogRecord) -> bool:
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in _STANDARD_ATTRS and not k.startswith("_")
        }
        if extras:
            try:
                formatted = record.getMessage()
                record.msg = f"{formatted} {json.dumps(extras, default=str)}"
                record.args = None
            except Exception:
                pass
        return True


class JSONFormatter(logging.Formatter):
    """Format logs as JSON for structured ContainerAppConsoleLogs output."""

    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                log_data[key] = value
        return json.dumps(log_data, default=str)


def setup_logging():
    """Configure structured stdout logging for the whole job process."""
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(JSONFormatter())

    log_level_str = os.getenv("LOGGING_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler.addFilter(_AppLogFilter())
    console_handler.addFilter(_ExtrasToMessageFilter())
    root_logger.addHandler(console_handler)
    return root_logger
