"""
Service Bus publisher — thin async wrapper over the azure-servicebus SDK.

Usage (from any async FastAPI endpoint):
    from service_bus_publisher import publish_event
    await publish_event("nomination.created", nomination_id)

Environment variables required (set via Terraform as plain env vars):
    SERVICE_BUS_FQNS         e.g. sb-award-sandbox.servicebus.windows.net
    SERVICE_BUS_TOPIC_NAME   e.g. award-events
    MI_CLIENT_ID             Client ID of the user-assigned managed identity
                             attached to this container. Passed explicitly to
                             DefaultAzureCredential so IMDS knows which MI to
                             use. Named MI_CLIENT_ID (not AZURE_CLIENT_ID) to
                             distinguish it from CLIENT_ID (the app registration
                             used for JWT audience validation).
                             Not required for local dev — DefaultAzureCredential
                             falls through to AzureCliCredential automatically.

Authentication: DefaultAzureCredential with managed_identity_client_id set from
MI_CLIENT_ID.  In ACA the backend MI must have "Azure Service Bus Data Sender"
on the topic.
"""

import json
import logging
import os
import uuid

from azure.identity.aio import DefaultAzureCredential
from azure.servicebus.aio import ServiceBusClient
from azure.servicebus import ServiceBusMessage

logger = logging.getLogger(__name__)

_FQNS  = os.environ.get("SERVICE_BUS_FQNS", "")
_TOPIC = os.environ.get("SERVICE_BUS_TOPIC_NAME", "")

# MI_CLIENT_ID is set by Terraform on ACA containers to disambiguate which
# user-assigned managed identity IMDS should use.  In local dev this is absent
# and DefaultAzureCredential falls through to AzureCliCredential instead.
_MI_CLIENT_ID = os.environ.get("MI_CLIENT_ID") or None


async def publish_event(
    event_type: str,
    nomination_id: int,
    extra: dict | None = None,
) -> None:
    """
    Publish a nomination domain event to the Service Bus topic.

    The message body is UTF-8 encoded JSON bytes (AMQP data section):
        b'{"event_type": "nomination.approved", "nomination_id": 42}'

    Optional ``extra`` fields are merged into the payload for events that
    carry additional data, e.g.:
        publish_event("payout.accepted", 42, {"payment_ref": "WD-2026-00123"})
        → b'{"event_type": "payout.accepted", "nomination_id": 42,
             "payment_ref": "WD-2026-00123"}'

    The auxiliary worker reads this, claims idempotency via ProcessedEvents,
    and dispatches to the appropriate handler.

    Raises:
        RuntimeError: if SERVICE_BUS_FQNS or SERVICE_BUS_TOPIC_NAME are not set.
        azure.servicebus.exceptions.ServiceBusError: on SDK-level failures.
    """
    if not _FQNS or not _TOPIC:
        raise RuntimeError(
            "SERVICE_BUS_FQNS and SERVICE_BUS_TOPIC_NAME must be set "
            "before calling publish_event()"
        )

    # Encode to bytes so the SDK frames the body as an AMQP data section.
    # Passing a str produces an AMQP value section, which the receiver cannot
    # reliably reassemble via b"".join(msg.body) — leading to JSON parse errors.
    body: dict = {"event_type": event_type, "nomination_id": nomination_id}
    if extra:
        body.update(extra)
    payload = json.dumps(body).encode("utf-8")

    msg = ServiceBusMessage(
        payload,
        message_id=str(uuid.uuid4()),
        content_type="application/json",
        application_properties={"event_type": event_type},
    )

    # Pass managed_identity_client_id explicitly so IMDS resolves the correct
    # user-assigned MI.  When None (local dev), DefaultAzureCredential skips
    # ManagedIdentityCredential and falls through to AzureCliCredential.
    credential = DefaultAzureCredential(managed_identity_client_id=_MI_CLIENT_ID)

    try:
        async with ServiceBusClient(_FQNS, credential) as client:
            async with client.get_topic_sender(_TOPIC) as sender:
                await sender.send_messages(msg)
        logger.info(
            "Published event type=%s nomination_id=%d message_id=%s",
            event_type, nomination_id, msg.message_id,
        )
    except Exception:
        logger.exception(
            "Failed to publish event type=%s nomination_id=%d",
            event_type, nomination_id,
        )
        raise
    finally:
        await credential.close()
