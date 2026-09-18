"""Shared model-artifact publication helpers."""

from __future__ import annotations

import os
from pathlib import Path


def upload_artifact(
    local_path: Path,
    *,
    blob_folder: str,
    blob_filename: str | None = None,
) -> bool:
    """Upload one artifact to the configured tenant-scoped model folder.

    Azure Container Apps use the configured managed identity. Local runs may
    instead provide ``AZURE_STORAGE_KEY``. Upload failure remains non-fatal to
    the helper so the calling model-family publisher can decide whether an
    incomplete bundle is safe to activate.
    """
    account = os.getenv("AZURE_STORAGE_ACCOUNT")
    container = os.getenv("MODEL_CONTAINER", "ml-models")

    if not account:
        print(
            f"  ⚠  AZURE_STORAGE_ACCOUNT not set — "
            f"skipping upload of {local_path.name}"
        )
        return False

    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        storage_key = os.getenv("AZURE_STORAGE_KEY")
        if storage_key:
            client = BlobServiceClient(
                account_url=f"https://{account}.blob.core.windows.net",
                credential=storage_key,
            )
        else:
            client = BlobServiceClient(
                account_url=f"https://{account}.blob.core.windows.net",
                credential=DefaultAzureCredential(
                    managed_identity_client_id=os.getenv("MI_CLIENT_ID")
                ),
            )

        blob_name = f"{blob_folder.strip('/')}/{blob_filename or local_path.name}"
        blob_client = client.get_blob_client(container=container, blob=blob_name)
        with local_path.open("rb") as stream:
            blob_client.upload_blob(stream, overwrite=True)

        print(
            f"  ✓ Uploaded '{local_path.name}' → "
            f"blob://{account}/{container}/{blob_name}"
        )
        return True
    except Exception as exc:
        print(f"  ✗ Blob upload failed for '{local_path.name}': {exc}")
        return False
