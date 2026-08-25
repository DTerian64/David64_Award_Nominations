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
# this dir are treated as third-party. The entrypoint and stage modules under
# modeling/ all live beneath this directory.
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
        # ensure_ascii=False: the default escapes every non-ASCII character, so
        # a check mark arrives in Log Analytics as \u2713 and a rule of box-drawing
        # characters as fifty copies of \u2500. JSON is UTF-8 by definition, so
        # emitting the characters directly is both valid and readable. Requires the
        # stdout reconfiguration in setup_logging() below.
        return json.dumps(log_data, default=str, ensure_ascii=False)


def setup_logging():
    """Configure structured stdout logging for the whole job process."""
    # Force UTF-8 on stdout before anything can write to it. Containers are
    # already UTF-8, but a Windows console defaults to the legacy code page, and
    # with ensure_ascii=False a single "✓" would raise UnicodeEncodeError from
    # inside the logging handler. errors="replace" degrades to "?" rather than
    # letting a log line take down the stage it is reporting on.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass  # non-reconfigurable stream (pytest capture, redirected pipe)

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
