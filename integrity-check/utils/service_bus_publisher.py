"""
Synchronous Service Bus publisher for the auxiliary worker.

The auxiliary service is a synchronous worker loop (no asyncio), so it cannot
use the backend's async publish_event().  This module provides an equivalent
synchronous implementation using the same azure-servicebus SDK in sync mode.

Environment variables (same as backend):
    SERVICE_BUS_FQNS          — sb-award-sandbox.servicebus.windows.net
    SERVICE_BUS_TOPIC_NAME    — award-events
    MI_CLIENT_ID              — user-assigned MI client ID (optional in local dev)
    AZURE_STORAGE_KEY         — not used here; kept for parity with other modules
"""

import json
import logging
import os
import uuid

from azure.servicebus import ServiceBusClient, ServiceBusMessage
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .azure_credential import credential

logger = logging.getLogger("auxiliary.service_bus_publisher")

_FQNS  = os.environ["SERVICE_BUS_FQNS"]
_TOPIC = os.environ["SERVICE_BUS_TOPIC_NAME"]


def publish_event(
    event_type:    str,
    nomination_id: int | None = None,
    extra:         dict | None = None,
) -> None:
    """
    Publish a domain event to the Service Bus topic (synchronous).

    Body is UTF-8-encoded JSON bytes so the receiver can reliably reassemble
    via b"".join(msg.body) — consistent with the backend's async publisher.
    """
    body: dict = {"event_type": event_type}
    if nomination_id is not None:
        body["nomination_id"] = nomination_id
    if extra:
        body.update(extra)

    payload = json.dumps(body).encode("utf-8")

    # Propagate the current trace context so auxiliary-service can link its
    # spans (approver email, HRBP alert) back to the originating nomination request.
    props: dict = {"event_type": event_type}
    TraceContextTextMapPropagator().inject(props)

    msg = ServiceBusMessage(
        payload,
        message_id=str(uuid.uuid4()),
        content_type="application/json",
        application_properties=props,
    )

    with ServiceBusClient(_FQNS, credential) as client:
        with client.get_topic_sender(_TOPIC) as sender:
            sender.send_messages(msg)

    logger.info(
        "Published event type=%s nomination_id=%s message_id=%s",
        event_type, nomination_id, msg.message_id,
    )
