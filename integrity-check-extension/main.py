"""Service Bus worker for asynchronous integrity-check extensions."""

from __future__ import annotations

import json
import logging
import os
import signal

from azure.monitor.opentelemetry import configure_azure_monitor
from azure.servicebus import AutoLockRenewer, ServiceBusClient, ServiceBusReceiveMode
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv

from config import Settings
from dispatcher import Settlement, dispatch
from extensions.gnn_explainer.artifacts import AzureBlobReader, BundleLoader
from utils import db
from utils.azure_credential import credential

load_dotenv()
logging.basicConfig(
    level=getattr(logging, os.getenv("LOGGING_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("integrity_check_extension.main")
if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    configure_azure_monitor(
        connection_string=os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"]
    )

_shutdown = False


def _stop(_signum, _frame):
    global _shutdown
    _shutdown = True


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


def _decode(message) -> dict:
    chunks = list(message.body)
    if chunks and isinstance(chunks[0], (bytes, bytearray)):
        raw = b"".join(chunks).decode("utf-8")
    else:
        raw = "".join(str(chunk) for chunk in chunks)
    return json.loads(raw)


def main() -> None:
    settings = Settings.from_env()
    blob_service = BlobServiceClient(
        account_url=f"https://{settings.storage_account}.blob.core.windows.net",
        credential=credential,
    )
    loader = BundleLoader(
        AzureBlobReader(blob_service.get_container_client(settings.model_container)),
        settings.max_artifact_bytes,
    )
    logger.info(
        "Integrity check extension worker starting: topic=%s subscription=%s",
        settings.service_bus_topic,
        settings.service_bus_subscription,
    )
    with AutoLockRenewer(
        max_lock_renewal_duration=settings.max_auto_lock_renewal_seconds
    ) as renewer, ServiceBusClient(
        fully_qualified_namespace=settings.service_bus_fqns,
        credential=credential,
        logging_enable=False,
    ) as client, client.get_subscription_receiver(
        topic_name=settings.service_bus_topic,
        subscription_name=settings.service_bus_subscription,
        receive_mode=ServiceBusReceiveMode.PEEK_LOCK,
        max_wait_time=settings.max_wait_seconds,
    ) as receiver:
        while not _shutdown:
            messages = receiver.receive_messages(
                max_message_count=settings.max_message_count,
                max_wait_time=settings.max_wait_seconds,
            )
            for message in messages:
                if _shutdown:
                    receiver.abandon_message(message)
                    continue
                try:
                    payload = _decode(message)
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
                    receiver.dead_letter_message(
                        message,
                        reason="InvalidJson",
                        error_description=str(exc)[:4096],
                    )
                    continue
                result = dispatch(
                    payload,
                    int(message.delivery_count or 1),
                    loader,
                    on_claim=lambda: renewer.register(receiver, message),
                )
                if result.settlement is Settlement.COMPLETE:
                    receiver.complete_message(message)
                elif result.settlement is Settlement.ABANDON:
                    receiver.abandon_message(message)
                elif result.settlement is Settlement.DEFER:
                    # Natural lock expiry spaces retries so a stale SQL claim
                    # can recover before max_delivery_count is exhausted.
                    continue
                else:
                    if result.request is not None:
                        db.insert_nomination_log(
                            result.request,
                            "ERROR",
                            "GNN explanation request dead-lettered",
                            {
                                "request_id": result.request.request_id,
                                "attempt": int(message.delivery_count or 1),
                                "reason": result.reason,
                            },
                        )
                    receiver.dead_letter_message(
                        message,
                        reason=(result.reason or "PermanentExtensionFailure")[:128],
                        error_description=(result.reason or "")[:4096],
                    )
    logger.info("Integrity check extension worker stopped")


if __name__ == "__main__":
    main()
