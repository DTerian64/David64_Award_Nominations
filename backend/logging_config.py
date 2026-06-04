# logging_config.py
import logging
import sys
import json
import os
from datetime import datetime

# Absolute path of this package's directory.
# Used by _AppLogFilter to identify records that originate from our own code.
_APP_DIR = os.path.abspath(os.path.dirname(__file__))

# Standard LogRecord attributes that are NOT application extras.
# Used by both the filter and the formatter to identify user-supplied data.
_STANDARD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
})


class _AppLogFilter(logging.Filter):
    """Only pass log records that originate from this application's own code.

    Records whose pathname falls outside _APP_DIR (i.e. third-party libraries,
    uvicorn internals, azure-sdk, OpenTelemetry, etc.) are dropped at the
    handler level so they never appear in stdout / ContainerAppConsoleLogs.

    Records from our own code get the 'App_Log: ' prefix, making them easy
    to isolate in Log Analytics:
        | where message startswith "App_Log:"
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if os.path.abspath(record.pathname).startswith(_APP_DIR):
            record.msg = f"App_Log: {record.msg}"
            return True
        return False  # drop — not our code


class _ExtrasToMessageFilter(logging.Filter):
    """Appends extra kwargs to the log message body as a JSON dict.

    The Azure Monitor OTel exporter does not reliably map Python logging
    `extra={}` fields to App Insights customDimensions.  By merging them into
    the message string here — before the record reaches any handler — the data
    is always visible in the `message` column of the `traces` table regardless
    of the exporter version.

    Example output:
        "Nomination created successfully {"nomination_id": 42, "hrbp_flagged": false}"
    """

    def filter(self, record: logging.LogRecord) -> bool:
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in _STANDARD_ATTRS and not k.startswith("_")
        }
        if extras:
            try:
                record.msg = f"{record.msg} {json.dumps(extras, default=str)}"
                record.args = None
            except Exception:
                pass  # Never let the filter break logging
        return True


class JSONFormatter(logging.Formatter):
    """Format logs as JSON for structured output in ContainerAppConsoleLogs."""

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

        # Include all non-standard extras as named fields.
        # _ExtrasToMessageFilter has already merged them into getMessage(),
        # so they also appear in the message string for App Insights.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                log_data[key] = value

        return json.dumps(log_data, default=str)


def setup_logging():
    """Configure application logging for the entire application."""

    json_formatter = JSONFormatter()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(json_formatter)

    log_level_str = os.getenv("LOGGING_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove any existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Attach filters to the handler so only our own code reaches stdout.
    # _AppLogFilter drops third-party records entirely and prefixes ours with
    # 'App_Log: ' for easy KQL filtering.
    # _ExtrasToMessageFilter merges extra={} kwargs into the message body so
    # they survive the App Insights OTel exporter into customDimensions.
    console_handler.addFilter(_AppLogFilter())
    console_handler.addFilter(_ExtrasToMessageFilter())

    root_logger.addHandler(console_handler)

    return root_logger
