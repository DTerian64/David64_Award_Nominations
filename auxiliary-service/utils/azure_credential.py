"""
utils/azure_credential.py — one process-wide Azure credential (ADR-0001).

Every consumer in this container (SQL via pyodbc, the Service Bus receiver and
publisher, Blob storage) shares this single instance:

  * `managed_identity_client_id` is declared here and NOWHERE else, so the
    user-assigned MI is selected consistently. (This container has no
    system-assigned identity, so a bare DefaultAzureCredential() cannot tell
    IMDS which identity to request.)
  * The credential caches and refreshes tokens internally, keyed by scope, so
    SQL (database.windows.net) and Service Bus (servicebus.azure.net) each get
    their own cached token from the same object.

In Azure the MI is resolved via MI_CLIENT_ID; locally MI_CLIENT_ID is unset and
DefaultAzureCredential falls back to az / VS Code login. No SQL passwords.
"""

import os

from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential(
    managed_identity_client_id=os.getenv("MI_CLIENT_ID")
)
