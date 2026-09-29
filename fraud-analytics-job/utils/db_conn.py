"""
fraud-analytics-job/utils/db_conn.py -- shared Azure SQL connection.

Entra token via DefaultAzureCredential: the job's Managed Identity in Azure
(selected by MI_CLIENT_ID) or the developer's az / VS Code login locally.
No SQL username/password.
"""
import os
import logging
import struct
from typing import Callable

import pyodbc
from azure.identity import DefaultAzureCredential

_SQL_COPT_SS_ACCESS_TOKEN = 1256
_AZURE_SQL_SCOPE          = "https://database.windows.net/.default"
logger = logging.getLogger(__name__)
# process_timeout applies only to the subprocess-based links in the
# DefaultAzureCredential chain — AzureCli / AzurePowerShell / AzureDeveloperCli.
# It is inert in Azure, where MI_CLIENT_ID is set and ManagedIdentityCredential
# answers over HTTP without spawning anything.
#
# It matters on a developer machine. azure-identity defaults it to 10 s, and
# every connect() re-invokes `az account get-access-token` rather than reusing an
# in-process token — so a run that opens several connections shells out several
# times. On Windows `az.cmd` is a batch wrapper around a cold Python interpreter,
# and 10 s is not reliably enough: observed 2026-08-21, the wake-up connection
# took 6.0 s and the next one, immediately after torch and PyG were imported,
# blew the timeout and failed the stage with CredentialUnavailableError.
_credential = DefaultAzureCredential(
    managed_identity_client_id=os.getenv("MI_CLIENT_ID"),
    process_timeout=int(os.getenv("AZURE_CLI_PROCESS_TIMEOUT", "30")),
)


def connect(timeout: int = 60) -> pyodbc.Connection:
    """Open an Azure SQL connection authenticated with an Entra access token."""
    token        = _credential.get_token(_AZURE_SQL_SCOPE).token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token)}s", len(token), token)
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={os.environ['SQL_SERVER']};"
        f"DATABASE={os.environ['SQL_DATABASE']};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        # ODBC idle resiliency is defense-in-depth for short-lived pauses and
        # failovers. Long CPU-bound stages must still close their read
        # connection and open a fresh publication connection.
        "ConnectRetryCount=3;"
        "ConnectRetryInterval=10;"
        f"Connection Timeout={timeout};"
    )
    return pyodbc.connect(conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct})


class RenewableConnection:
    """Lazy DB-API connection that can be discarded and reopened safely.

    This is useful for phased workloads that read data, perform long-running
    non-database work, and later publish results. ``release`` ends the current
    transaction and closes the physical session; the next ``cursor`` or
    ``commit`` call opens a fresh connection through the supplied factory.
    """

    def __init__(
        self,
        factory: Callable[[], object] = connect,
        *,
        log_context: str = "database operation",
    ):
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
        """Roll back any read transaction and close the physical session."""
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
        """Close the current physical connection without trying to reuse it."""
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
