"""
azure_blob.py
─────────────
Handles upload of export files to Azure Blob Storage and generates
short-lived SAS download URLs.

Container : award-nomination-extracts
Account   : awardnominationmodels (rg_award_nomination)

Env vars required (same storage account already used for ml-models):
    AZURE_STORAGE_ACCOUNT    = awardnominationmodels
    AZURE_STORAGE_KEY        = <access key from portal>

Optional:
    BLOB_SAS_EXPIRY_HOURS    = 24   (default: 24 hours)
    BLOB_DELETE_LOCAL        = true (default: true — remove local file after upload)
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from azure.storage.blob import (
    BlobServiceClient,
    BlobSasPermissions,
    generate_blob_sas,
)

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
STORAGE_ACCOUNT   = os.getenv("AZURE_STORAGE_ACCOUNT", "awardnominationmodels")
STORAGE_KEY       = os.getenv("AZURE_STORAGE_KEY", "")
CONTAINER_NAME    = os.getenv("BLOB_CONTAINER_NAME", "award-nomination-extracts")
SAS_EXPIRY_HOURS  = int(os.getenv("BLOB_SAS_EXPIRY_HOURS", "24"))
DELETE_LOCAL      = os.getenv("BLOB_DELETE_LOCAL", "true").lower() == "true"

_blob_service: BlobServiceClient | None = None


def _get_service() -> BlobServiceClient:
    """Lazy singleton BlobServiceClient."""
    global _blob_service
    if _blob_service is None:
        if not STORAGE_KEY:
            raise EnvironmentError(
                "AZURE_STORAGE_KEY is not set. "
                "Add it to your .env file — find it in the Azure portal under "
                f"Storage account '{STORAGE_ACCOUNT}' → Access keys."
            )
        account_url = f"https://{STORAGE_ACCOUNT}.blob.core.windows.net"
        _blob_service = BlobServiceClient(
            account_url   = account_url,
            credential    = STORAGE_KEY,
        )
        logger.info("azure_blob: BlobServiceClient initialised (%s)", account_url)
    return _blob_service


def _ensure_container() -> None:
    """Create the container if it doesn't exist yet."""
    service = _get_service()
    container = service.get_container_client(CONTAINER_NAME)
    try:
        container.get_container_properties()
    except Exception:
        logger.info("azure_blob: creating container '%s'", CONTAINER_NAME)
        container.create_container()


def upload_export(file_path: Path) -> str:
    """
    Upload a local export file to Azure Blob Storage.

    Args:
        file_path: Path to the local file to upload.

    Returns:
        A SAS URL valid for SAS_EXPIRY_HOURS hours — safe to return to the client.

    Raises:
        Exception on upload failure (let the caller handle/log).
    """
    _ensure_container()
    service   = _get_service()
    blob_name = file_path.name   # just the filename, no path

    # ── Upload ────────────────────────────────────────────────────────────────
    blob_client = service.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
    with open(file_path, "rb") as f:
        blob_client.upload_blob(f, overwrite=True)

    file_size = file_path.stat().st_size
    logger.info("azure_blob: uploaded '%s' → %s/%s (%d bytes)",
                blob_name, CONTAINER_NAME, blob_name, file_size)

    # ── Generate SAS URL ──────────────────────────────────────────────────────
    expiry = datetime.now(timezone.utc) + timedelta(hours=SAS_EXPIRY_HOURS)

    sas_token = generate_blob_sas(
        account_name   = STORAGE_ACCOUNT,
        container_name = CONTAINER_NAME,
        blob_name      = blob_name,
        account_key    = STORAGE_KEY,
        permission     = BlobSasPermissions(read=True),
        expiry         = expiry,
    )

    download_url = (
        f"https://{STORAGE_ACCOUNT}.blob.core.windows.net"
        f"/{CONTAINER_NAME}/{blob_name}?{sas_token}"
    )

    logger.info("azure_blob: SAS URL generated (expires in %dh)", SAS_EXPIRY_HOURS)

    # ── Clean up local file ───────────────────────────────────────────────────
    if DELETE_LOCAL:
        file_path.unlink()
        logger.info("azure_blob: local file deleted — %s", blob_name)

    return download_url