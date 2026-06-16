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

logger = logging.getLogger("auxiliary.cert_blob")

_ACCOUNT   = os.getenv("AZURE_STORAGE_ACCOUNT")
_KEY       = os.getenv("AZURE_STORAGE_KEY")
_CONTAINER = os.getenv("CERTIFICATES_CONTAINER", "certificates")


def _blob_name(tenant_id: int, nomination_id: int) -> str:
    return f"{tenant_id}/nomination_{nomination_id}.pdf"


def download_certificate(tenant_id: int, nomination_id: int) -> bytes | None:
    """
    Return the certificate PDF bytes, or None if the blob doesn't exist or
    storage isn't configured. Never raises — a missing certificate must not
    fail the beneficiary email.
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
        if not blob.exists():
            logger.warning(
                "Certificate blob not found",
                extra={"tenant_id": tenant_id, "nomination_id": nomination_id},
            )
            return None
        return blob.download_blob().readall()
    except Exception as e:
        logger.warning(
            "Certificate download failed for nomination %d: %s", nomination_id, e
        )
        return None
