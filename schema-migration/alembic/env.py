"""
schema-migration/alembic/env.py
-------------------------------
Standalone Alembic environment for the Award Nomination database (ADR-0001).
Models-free: migrations are hand-written and applied with `alembic upgrade head`,
so target_metadata is None.

Auth: always Entra via DefaultAzureCredential -- it resolves the assigned Managed
Identity inside Azure (MI_CLIENT_ID selects the user-assigned MI) and your
az / VS Code login locally. No SQL username/password, no toggles.
"""

import os
import struct
from urllib.parse import quote_plus

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from azure.identity import DefaultAzureCredential

from dotenv import load_dotenv
load_dotenv()  # loads SQL_SERVER / SQL_DATABASE from schema-migration/.env for local runs

config = context.config
target_metadata = None  # hand-written migrations only

DB_SERVER = os.environ["SQL_SERVER"]
DB_NAME   = os.environ["SQL_DATABASE"]
DB_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")

SQL_COPT_SS_ACCESS_TOKEN = 1256
AZURE_SQL_SCOPE = "https://database.windows.net/.default"

_credential = DefaultAzureCredential(
    managed_identity_client_id=os.getenv("MI_CLIENT_ID")
)


def _odbc() -> str:
    return (
        f"Driver={{{DB_DRIVER}}};"
        f"Server={DB_SERVER};"
        f"Database={DB_NAME};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no;"
    )


def _token_struct() -> bytes:
    token = _credential.get_token(AZURE_SQL_SCOPE).token.encode("utf-16-le")
    return struct.pack(f"<I{len(token)}s", len(token), token)


def run_migrations_online() -> None:
    engine = create_engine(
        f"mssql+pyodbc:///?odbc_connect={quote_plus(_odbc())}",
        poolclass=NullPool,
        connect_args={"attrs_before": {SQL_COPT_SS_ACCESS_TOKEN: _token_struct()}},
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql` -- emits SQL, no DB connection."""
    context.configure(
        url=f"mssql+pyodbc:///?odbc_connect={quote_plus(_odbc())}",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
