"""Shared Azure SQL connection primitives.

Callers must name the server and database environment variables explicitly.
This keeps source-system connections separate from Integrity Sentinel storage
even while both point at the same physical database during the transition.
"""

from __future__ import annotations

import logging
import os
import struct
from collections.abc import Callable

import pyodbc
from azure.identity import DefaultAzureCredential


_SQL_COPT_SS_ACCESS_TOKEN = 1256
_AZURE_SQL_SCOPE = "https://database.windows.net/.default"
logger = logging.getLogger(__name__)

_credential = DefaultAzureCredential(
    managed_identity_client_id=os.getenv("MI_CLIENT_ID"),
    process_timeout=int(os.getenv("AZURE_CLI_PROCESS_TIMEOUT", "30")),
)


def connect_from_environment(
    server_variable: str,
    database_variable: str,
    timeout: int = 60,
) -> pyodbc.Connection:
    """Open an Entra-authenticated SQL connection from an explicit env pair."""

    server = os.environ[server_variable]
    database = os.environ[database_variable]
    token = _credential.get_token(_AZURE_SQL_SCOPE).token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token)}s", len(token), token)
    connection_string = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "ConnectRetryCount=3;"
        "ConnectRetryInterval=10;"
        f"Connection Timeout={timeout};"
    )
    return pyodbc.connect(
        connection_string,
        attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct},
    )


class RenewableConnection:
    """Lazy DB-API connection that can be discarded and reopened safely."""

    def __init__(
        self,
        factory: Callable[[], object],
        *,
        log_context: str = "database operation",
    ) -> None:
        self._factory = factory
        self._log_context = log_context
        self._connection = None

    def _get(self):
        if self._connection is None:
            logger.info("%s: opening SQL connection", self._log_context)
            self._connection = self._factory()
        return self._connection

    def cursor(self):
        return self._get().cursor()

    def commit(self) -> None:
        self._get().commit()

    def rollback(self) -> None:
        if self._connection is not None:
            self._connection.rollback()

    def release(self) -> None:
        """Roll back a read transaction and close the physical session."""

        if self._connection is None:
            return
        try:
            self._connection.rollback()
        except Exception as exc:
            logger.warning(
                "%s: SQL rollback failed during release: %s",
                self._log_context,
                exc,
            )
        finally:
            self.discard()

    def discard(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                logger.debug(
                    "%s: SQL connection close failed during discard",
                    self._log_context,
                    exc_info=True,
                )

    def close(self) -> None:
        self.discard()
