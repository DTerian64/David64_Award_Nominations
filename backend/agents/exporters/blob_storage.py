import os
from azure.storage.blob import BlobServiceClient, BlobSasPermissions, generate_blob_sas, ContentSettings
from datetime import datetime, timedelta, timezone

import logging
logger = logging.getLogger(__name__)

_ACCOUNT   = os.getenv("AZURE_STORAGE_ACCOUNT")
_KEY       = os.getenv("AZURE_STORAGE_KEY")
_CONTAINER = os.getenv("EXTRACTS_CONTAINER", "award-nomination-extracts")
_SAS_EXPIRY_HOURS = int(os.getenv("BLOB_SAS_EXPIRY_HOURS", "24"))


async def upload_to_blob(data, filename, content_type="application/octet-stream"):
    if not data:
        return {"status": "error", "message": "No data to upload."}
    if not _ACCOUNT or not _KEY:
        return {"status": "error", "message": "AZURE_STORAGE_ACCOUNT or AZURE_STORAGE_KEY is not set."}

    try:
        conn_str = f"DefaultEndpointsProtocol=https;AccountName={_ACCOUNT};AccountKey={_KEY};EndpointSuffix=core.windows.net"
        service_client = BlobServiceClient.from_connection_string(conn_str)
        container_client = service_client.get_container_client(_CONTAINER)

        if not container_client.exists():
            container_client.create_container()

        container_client.get_blob_client(filename).upload_blob(
            data, overwrite=True,
            content_settings=ContentSettings(content_type=content_type,
                                             content_disposition = f'attachment; filename="{filename}"'
                                            )
        )

        sas_token = generate_blob_sas(
            account_name   = _ACCOUNT,
            container_name = _CONTAINER,
            blob_name      = filename,
            account_key    = _KEY,
            permission     = BlobSasPermissions(read=True),
            expiry         = datetime.now(timezone.utc) + timedelta(hours=_SAS_EXPIRY_HOURS),
        )

        return {
            "status":          "success",
            "download_url":    f"https://{_ACCOUNT}.blob.core.windows.net/{_CONTAINER}/{filename}?{sas_token}",
            "file_size_bytes": len(data),
            "filename":        filename,
        }

    except Exception as e:
        logger.error("blob_storage: upload failed for %s: %s", filename, e, exc_info=True)
        return {"status": "error", "message": str(e)}