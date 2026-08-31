"""
utils/azure_credential.py — process-wide Azure credentials (ADR-0001).

payroll-broker talks to Azure two ways, so it needs two shared instances:

  * `credential`        — sync DefaultAzureCredential, for the SQL pyodbc token
                          in utils/sqlhelper.py.
  * `async_credential`  — aio DefaultAzureCredential, for the aio Service Bus
                          clients (publisher + worker receiver). A sync
                          credential cannot back an async client, hence two.

`managed_identity_client_id` is declared here and nowhere else, selecting the
user-assigned MI consistently (the container has no system-assigned identity).
Both are module-level singletons that cache/refresh tokens per scope and live
for the process; the async credential's aiohttp session is reclaimed at exit,
so callers must NOT close it per operation.

In Azure the MI is resolved via MI_CLIENT_ID; locally, unset, it falls back to
az / VS Code login. No SQL passwords.
"""

import os

from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AioDefaultAzureCredential

_MI_CLIENT_ID = os.getenv("MI_CLIENT_ID")

credential = DefaultAzureCredential(managed_identity_client_id=_MI_CLIENT_ID)
async_credential = AioDefaultAzureCredential(managed_identity_client_id=_MI_CLIENT_ID)
