"""One process-wide credential for SQL, Blob Storage, and Service Bus."""

import os
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential(managed_identity_client_id=os.getenv("MI_CLIENT_ID"))
