"""
schema-migration/alembic/env.py
-------------------------------
Standalone Alembic environment for the Award Nomination database (ADR-0001).
Models-free: migrations are hand-written and applied with `alembic upgrade head`,
so target_metadata is None.

Auth: always Entra via DefaultAzureCredential -- it resolves the assigned Managed
Identity inside Azure (MI_CLIENT_ID selects the user-assigned MI) and your
az / VS Code login locally. No SQL username/password, no toggles.

Logging & errors: alembic.ini's logging config is activated here (so alembic's
own "Running upgrade X -> Y" lines are emitted), and a "schema-migration" logger
reports the target server/database, token acquisition, and success/failure.
Every failure path logs a full traceback and re-raises, so the process exits
non-zero and the Container Apps Job execution is marked Failed (never a silent
green run).
"""

import logging
import os
import struct
import time
from logging.config import fileConfig
from urllib.parse import quote_plus

import pyodbc

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ClientAuthenticationError

from dotenv import load_dotenv

load_dotenv()  # loads SQL_SERVER / SQL_DATABASE from schema-migration/.env for local runs

config = context.config

# Activate alembic.ini logging (root / sqlalchemy / alembic loggers), then add
# our own INFO logger for environment-level messages.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
logger = logging.getLogger("schema-migration")
logger.setLevel(logging.INFO)

target_metadata = None  # hand-written migrations only

SQL_COPT_SS_ACCESS_TOKEN = 1256
AZURE_SQL_SCOPE = "https://database.windows.net/.default"


def _require_env(name: str) -> str:
    """Return a required env var, or fail loudly with a non-zero exit."""
    value = os.getenv(name)
    if not value:
        logger.error("Required environment variable %s is not set.", name)
        raise SystemExit(f"schema-migration: missing required environment variable {name!r}")
    return value


DB_SERVER    = _require_env("SQL_SERVER")
DB_NAME      = _require_env("SQL_DATABASE")
DB_DRIVER    = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
MI_CLIENT_ID = os.getenv("MI_CLIENT_ID")

_credential = DefaultAzureCredential(managed_identity_client_id=MI_CLIENT_ID)


def _odbc() -> str:
    return (
        f"Driver={{{DB_DRIVER}}};"
        f"Server={DB_SERVER};"
        f"Database={DB_NAME};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no;"
        f"Connection Timeout=30;"
    )


def _token_struct() -> bytes:
    """Acquire an Entra access token for Azure SQL and pack it for the ODBC driver."""
    try:
        token = _credential.get_token(AZURE_SQL_SCOPE).token.encode("utf-16-le")
    except ClientAuthenticationError:
        logger.exception(
            "Failed to acquire an Entra token for %s (MI_CLIENT_ID=%s). Verify the "
            "migration Managed Identity is attached to the job and is a member of "
            "the sql-migrations group (db_ddladmin).",
            AZURE_SQL_SCOPE, MI_CLIENT_ID or "<unset>",
        )
        raise
    return struct.pack(f"<I{len(token)}s", len(token), token)


# Transient Azure SQL conditions worth retrying — chiefly the serverless (GP_S)
# auto-resume (40613, "not currently available"), plus common transient/throttle
# codes and login timeouts. See Microsoft's transient-error guidance.
_TRANSIENT_MARKERS = (
    "40613", "40197", "40501", "40540", "10928", "10929",
    "49918", "49919", "49920", "4060", "4221",
    "not currently available", "is not currently available",
    "timeout expired", "login timeout",
)


def _wait_for_db(max_wait_seconds: int = 180) -> None:
    """Ensure the database is awake before migrating.

    The database is serverless (GP_S) with auto-pause, so the first connection
    after idle triggers a resume that takes ~30-60s and fails until ready. Poll
    a lightweight SELECT 1 with exponential backoff, retrying only transient
    conditions; anything else (e.g. an auth/login failure) fails fast.
    """
    deadline = time.monotonic() + max_wait_seconds
    attempt = 0
    delay = 3
    while True:
        attempt += 1
        try:
            conn = pyodbc.connect(
                _odbc(),
                attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _token_struct()},
            )
            conn.cursor().execute("SELECT 1").fetchone()
            conn.close()
            logger.info("Database is reachable (attempt %d).", attempt)
            return
        except pyodbc.Error as exc:
            message = str(exc)
            transient = any(m in message.lower() for m in _TRANSIENT_MARKERS)
            if not transient or time.monotonic() >= deadline:
                logger.error(
                    "Database not reachable after %d attempt(s) / %ds: %s",
                    attempt, max_wait_seconds, message,
                )
                raise
            logger.warning(
                "Database not ready (attempt %d) - likely a serverless resume; "
                "retrying in %ds. Detail: %s",
                attempt, delay, message,
            )
            time.sleep(delay)
            delay = min(delay * 2, 20)


def run_migrations_online() -> None:
    logger.info(
        "Applying migrations -> server=%s database=%s driver=%r identity=%s",
        DB_SERVER, DB_NAME, DB_DRIVER,
        f"MI {MI_CLIENT_ID}" if MI_CLIENT_ID else "local az / VS Code login",
    )
    _wait_for_db()
    try:
        engine = create_engine(
            f"mssql+pyodbc:///?odbc_connect={quote_plus(_odbc())}",
            poolclass=NullPool,
            connect_args={"attrs_before": {SQL_COPT_SS_ACCESS_TOKEN: _token_struct()}},
        )
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    except Exception:
        logger.exception("Schema migration FAILED for database %s on %s.", DB_NAME, DB_SERVER)
        raise
    logger.info("Schema migration completed successfully for database %s.", DB_NAME)


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql` -- emits SQL, no DB connection."""
    logger.info("Rendering migrations offline (SQL only) for database=%s.", DB_NAME)
    try:
        context.configure(
            url=f"mssql+pyodbc:///?odbc_connect={quote_plus(_odbc())}",
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )
        with context.begin_transaction():
            context.run_migrations()
    except Exception:
        logger.exception("Offline migration render FAILED for database %s.", DB_NAME)
        raise
    logger.info("Offline migration render completed for database %s.", DB_NAME)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
