"""
Service Bus publisher — thin async wrapper over the azure-servicebus SDK.

Usage (from any async FastAPI endpoint):
    from service_bus_publisher import publish_event
    await publish_event("nomination.created", nomination_id)

Environment variables required (set via Terraform as plain env vars):
    SERVICE_BUS_FQNS         e.g. award-sb-sandbox.servicebus.windows.net
    SERVICE_BUS_TOPIC_NAME   e.g. nomination-events

Authentication: DefaultAzureCredential.  In ACA the backend's managed identity
must have "Azure Service Bus Data Sender" on the topic (or namespace).
"""

import json
import logging
import os
import uuid

from azure.identity.aio import DefaultAzureCredential
from azure.servicebus.aio import ServiceBusClient
from azure.servicebus import ServiceBusMessage

logger = logging.getLogger(__name__)

_FQNS = os.environ.get("SERVICE_BUS_FQNS", "")
_TOPIC = os.environ.get("SERVICE_BUS_TOPIC_NAME", "")


async def publish_event(event_type: str, nomination_id: int) -> None:
    """
    Publish a nomination domain event to the Service Bus topic.

    The message body is a JSON object:
        {"event_type": "nomination.created", "nomination_id": 42}

    The auxiliary worker reads this, claims idempotency via ProcessedEvents,
    fetches fresh data from SQL, and sends the appropriate email.

    Raises:
        RuntimeError: if SERVICE_BUS_FQNS or SERVICE_BUS_TOPIC_NAME are not set.
        azure.servicebus.exceptions.ServiceBusError: on SDK-level failures.
    """
    if not _FQNS or not _TOPIC:
        raise RuntimeError(
            "SERVICE_BUS_FQNS and SERVICE_BUS_TOPIC_NAME must be set "
            "before calling publish_event()"
        )

    payload = json.dumps({
        "event_type": event_type,
        "nomination_id": nomination_id,
    })

    msg = ServiceBusMessage(
        payload,
        message_id=str(uuid.uuid4()),
        content_type="application/json",
        application_properties={"event_type": event_type},
    )

    client_id = os.getenv("CLIENT_ID")
    credential = DefaultAzureCredential(
        managed_identity_client_id=client_id if client_id else None,
        logging_enable=True,
    )
    
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
