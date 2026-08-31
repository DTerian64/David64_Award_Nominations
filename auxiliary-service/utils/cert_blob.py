"""
Certificate blob download for the auxiliary worker.

Read-only access to the generated award certificate PDFs in the `certificates`
container. The backend (utils/certificate.py) is responsible for *generating*
the certificate; the worker only downloads a copy to attach to the beneficiary
email when a tenant has opted into attachments.

Blob naming MUST match backend utils/certificate.certificate_blob_name():
    {tenant_id}/nomination_{nomination_id}.pdf
"""

import logging
import os
import time

logger = logging.getLogger("auxiliary.cert_blob")

_ACCOUNT   = os.getenv("AZURE_STORAGE_ACCOUNT")
_KEY       = os.getenv("AZURE_STORAGE_KEY")
_CONTAINER = os.getenv("CERTIFICATES_CONTAINER", "certificates")


def _blob_name(tenant_id: int, nomination_id: int) -> str:
    return f"{tenant_id}/nomination_{nomination_id}.pdf"


def download_certificate(
    tenant_id: int, nomination_id: int, attempts: int = 1, delay: float = 2.0
) -> bytes | None:
    """
    Return the certificate PDF bytes, or None if the blob doesn't exist or
    storage isn't configured. Never raises — a missing certificate must not
    fail the beneficiary email.

    attempts/delay: the backend generates the certificate at approval time, just
    before publishing nomination.approved. If the worker consumes that event and
    reads the blob within the small window before it's visible, the first check
    can miss. Retrying a few times with a short delay closes that race without
    risking duplicate emails (it all happens within one handler execution).
    """
    if not _ACCOUNT or not _KEY:
        logger.warning("AZURE_STORAGE_ACCOUNT/KEY not set — cannot download certificate")
        return None

    try:
        # Imported lazily so the worker has no hard dependency on the Azure SDK
        # unless a tenant actually uses certificate attachments.
        from azure.storage.blob import BlobServiceClient

        conn_str = (
            f"DefaultEndpointsProtocol=https;AccountName={_ACCOUNT};"
            f"AccountKey={_KEY};EndpointSuffix=core.windows.net"
        )
        blob = (
            BlobServiceClient.from_connection_string(conn_str)
            .get_container_client(_CONTAINER)
            .get_blob_client(_blob_name(tenant_id, nomination_id))
        )
        for attempt in range(1, max(1, attempts) + 1):
            if blob.exists():
                return blob.download_blob().readall()
            if attempt < attempts:
                time.sleep(delay)
        logger.warning(
            "Certificate blob not found after %d attempt(s)",
            attempts,
            extra={"tenant_id": tenant_id, "nomination_id": nomination_id},
        )
        return None
    except Exception as e:
        logger.warning(
            "Certificate download failed for nomination %d: %s", nomination_id, e
        )
        return None
