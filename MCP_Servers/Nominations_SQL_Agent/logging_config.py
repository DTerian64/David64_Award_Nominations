"""
logging_config.py
─────────────────
Centralised logging setup for the Nominations SQL Agent.

Usage:
    from logging_config import setup_logging, get_logger

    setup_logging()                          # call once at startup
    logger = get_logger("my_module")         # call anywhere

Environment variables:
    LOG_LEVEL   — stderr verbosity (default: INFO)
                  Set to DEBUG to see full prompts & raw AI responses
    LOG_FILE    — log file path   (default: nominations_agent.log)
                  Set to "" or "none" to disable file logging
"""

import os
import logging
from logging.handlers import TimedRotatingFileHandler

# ─────────────────────────────────────────────
# Custom formatter — emoji prefix for fast visual scanning
# ─────────────────────────────────────────────
class EmojiFormatter(logging.Formatter):
    EMOJIS = {
        logging.DEBUG:    "🔍",
        logging.INFO:     "ℹ️ ",
        logging.WARNING:  "⚠️ ",
        logging.ERROR:    "❌",
        logging.CRITICAL: "🔥",
    }

    def format(self, record: logging.LogRecord) -> str:
        record.emoji = self.EMOJIS.get(record.levelno, "  ")
        return super().format(record)


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
LOG_FORMAT  = "%(asctime)s %(emoji)s [%(levelname)-8s] %(name)s — %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False   # guard against calling setup_logging() more than once


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────
def setup_logging() -> None:
    """
    Configure the root logger with:
      - A rotating file handler (always DEBUG, rotates at midnight, kept 7 days)
      - A stderr handler       (level from LOG_LEVEL env var, default INFO)

    Safe to call multiple times — only configures once.
    NOTE: MCP uses stdout for its protocol, so we write to stderr to avoid conflicts.
    """
    global _configured
    if _configured:
        return
    _configured = True

    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level      = getattr(logging, log_level_name, logging.INFO)
    log_file       = os.environ.get("LOG_FILE", "nominations_agent.log")

    formatter = EmojiFormatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)
    handlers: list[logging.Handler] = []

    # ── File handler ──────────────────────────
    if log_file and log_file.lower() != "none":
        file_handler = TimedRotatingFileHandler(
            log_file, when="midnight", backupCount=7, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)   # file always captures everything
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    # ── Stderr handler ────────────────────────
    stderr_handler = logging.StreamHandler()
    stderr_handler.setLevel(log_level)
    stderr_handler.setFormatter(formatter)
    handlers.append(stderr_handler)

    # ── Root logger ───────────────────────────
    logging.basicConfig(level=logging.DEBUG, handlers=handlers)

    # Emit a startup banner through the root logger
    startup_log = logging.getLogger("nominations_agent")
    startup_log.info("=" * 60)
    startup_log.info("Nominations SQL Agent — logging initialised")
    startup_log.info(
        "Level: %s (stderr) | DEBUG (file: %s)",
        log_level_name,
        log_file if log_file and log_file.lower() != "none" else "disabled",
    )
    startup_log.info("=" * 60)


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger namespaced under 'nominations_agent'.

    Examples:
        get_logger("server")        →  nominations_agent.server
        get_logger("generate_sql")  →  nominations_agent.generate_sql
    """
    return logging.getLogger(f"nominations_agent.{name}")
