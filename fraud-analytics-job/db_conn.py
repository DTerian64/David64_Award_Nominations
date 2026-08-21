"""
fraud-analytics-job/db_conn.py -- shared Azure SQL connection (ADR-0001).

Entra token via DefaultAzureCredential: the job's Managed Identity in Azure
(selected by MI_CLIENT_ID) or the developer's az / VS Code login locally.
No SQL username/password.
"""
import os
import struct

import pyodbc
from azure.identity import DefaultAzureCredential

_SQL_COPT_SS_ACCESS_TOKEN = 1256
_AZURE_SQL_SCOPE          = "https://database.windows.net/.default"
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
        f"Connection Timeout={timeout};"
    )
    return pyodbc.connect(conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: token_struct})
