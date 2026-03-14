"""
alembic/env.py
--------------
Alembic environment configuration for the Award Nomination backend.

• Reads the same SQL_SERVER / SQL_DATABASE / SQL_USER / SQL_PASSWORD /
  USE_MANAGED_IDENTITY env vars as sqlhelper2.py.
• Imports the SQLAlchemy metadata from sqlhelper2.py so that autogenerate
  can diff the ORM models against the live database.

Usage (from the backend/ directory):

  # Generate a new migration after editing ORM models:
  alembic revision --autogenerate -m "describe change"

  # Apply all pending migrations:
  alembic upgrade head

  # Roll back one step:
  alembic downgrade -1
"""

import os
import struct
from logging.config import fileConfig
from urllib.parse import quote_plus

from alembic import context
from sqlalchemy import create_engine, pool

# ---------------------------------------------------------------------------
# Alembic Config object (alembic.ini)
# ---------------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging if present.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Import metadata from the ORM models so autogenerate can detect schema diffs
# ---------------------------------------------------------------------------
from sqlhelper2 import Base  # noqa: E402  (after sys.path manipulation below)

target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Connection helper — mirrors the three-branch logic in sqlhelper2._build_engine
# ---------------------------------------------------------------------------
DB_SERVER   = os.getenv("SQL_SERVER")
DB_NAME     = os.getenv("SQL_DATABASE")
DB_DRIVER   = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
DB_USERNAME = os.getenv("SQL_USER")
DB_PASSWORD = os.getenv("SQL_PASSWORD")
USE_MANAGED_IDENTITY = os.getenv("USE_MANAGED_IDENTITY", "false").lower() == "true"


def _get_url() -> str:
    """Return a SQLAlchemy connection URL string (SQL auth branch only)."""
    if DB_USERNAME and DB_PASSWORD:
        odbc_str = (
            f"Driver={{{DB_DRIVER}}};"
            f"Server={DB_SERVER};"
            f"Database={DB_NAME};"
            f"UID={DB_USERNAME};"
            f"PWD={DB_PASSWORD};"
            f"Encrypt=yes;"
            f"TrustServerCertificate=no;"
        )
        return f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc_str)}"
    else:
        # Azure AD Interactive — good enough for local migration runs
        odbc_str = (
            f"Driver={{{DB_DRIVER}}};"
            f"Server={DB_SERVER};"
            f"Database={DB_NAME};"
            f"Authentication=ActiveDirectoryInteractive;"
            f"Encrypt=yes;"
            f"TrustServerCertificate=no;"
        )
        return f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc_str)}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL script, no DB connection)."""
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connects to DB and applies changes)."""
    if USE_MANAGED_IDENTITY:
        # Managed Identity: reuse the creator callable from sqlhelper2
        from sqlhelper2 import engine as app_engine  # noqa: E402
        connectable = app_engine
    else:
        connectable = create_engine(
            _get_url(),
            poolclass=pool.NullPool,
        )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
