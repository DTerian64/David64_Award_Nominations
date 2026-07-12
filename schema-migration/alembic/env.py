"""
schema-migration/alembic/env.py
-------------------------------
Standalone Alembic environment for the Award Nomination database.

This project owns the database schema (ADR-0001). It is intentionally
**models-free**: migrations are hand-written and applied with
`alembic upgrade head`, so there is no ORM metadata to diff against and
`target_metadata` is None. (If autogenerate is ever reintroduced, import a
shared models package here and set target_metadata accordingly.)

Authentication (chosen by environment):
  • SQL auth        – SQL_USER + SQL_PASSWORD set        (local dev)
  • Entra token     – otherwise                          (CI / Managed Identity)
                      Uses DefaultAzureCredential, which resolves the GitHub
                      Actions OIDC `az login` session in CI, a Managed Identity
                      inside Azure, or your `az login` / VS Code identity locally.
"""

import os
import struct
from urllib.parse import quote_plus

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from dotenv import load_dotenv
load_dotenv()  # loads schema-migration/.env if present, silently skips if not

config = context.config

# Hand-written migrations only — no ORM metadata.
target_metadata = None

DB_SERVER   = os.getenv("SQL_SERVER")
DB_NAME     = os.getenv("SQL_DATABASE")
DB_DRIVER   = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
DB_USERNAME = os.getenv("SQL_USER")
DB_PASSWORD = os.getenv("SQL_PASSWORD")

# ODBC connection attribute for an Entra access token (SQL_COPT_SS_ACCESS_TOKEN).
SQL_COPT_SS_ACCESS_TOKEN = 1256
AZURE_SQL_SCOPE = "https://database.windows.net/.default"


def _base_odbc(auth_clause: str = "") -> str:
    return (
        f"Driver={{{DB_DRIVER}}};"
        f"Server={DB_SERVER};"
        f"Database={DB_NAME};"
        f"{auth_clause}"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no;"
    )


def _make_engine():
    """SQL auth when a username/password is present; otherwise an Entra token."""
    if DB_USERNAME and DB_PASSWORD:
        odbc = _base_odbc(f"UID={DB_USERNAME};PWD={DB_PASSWORD};")
        url = f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc)}"
        return create_engine(url, poolclass=NullPool)

    # Token-based: works for the CI federated identity, ACA Managed Identity,
    # and a local developer's az/VS Code login.
    from azure.identity import DefaultAzureCredential

    token = DefaultAzureCredential().get_token(AZURE_SQL_SCOPE).token
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

    url = f"mssql+pyodbc:///?odbc_connect={quote_plus(_base_odbc())}"
    return create_engine(
        url,
        poolclass=NullPool,
        connect_args={"attrs_before": {SQL_COPT_SS_ACCESS_TOKEN: token_struct}},
    )


def run_migrations_offline() -> None:
    """Emit SQL without a DB connection (`alembic upgrade head --sql`)."""
    if DB_USERNAME and DB_PASSWORD:
        url = f"mssql+pyodbc:///?odbc_connect={quote_plus(_base_odbc(f'UID={DB_USERNAME};PWD={DB_PASSWORD};'))}"
    else:
        url = f"mssql+pyodbc:///?odbc_connect={quote_plus(_base_odbc())}"
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect and apply migrations."""
    connectable = _make_engine()
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
