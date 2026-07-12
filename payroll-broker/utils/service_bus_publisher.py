"""
service_bus_publisher.py — thin async wrapper over the azure-servicebus SDK.

Identical in structure to the backend publisher.  The payroll broker uses this
to emit payroll.accepted / payroll.failed events after Gusto webhooks arrive.

Environment variables required:
    SERVICE_BUS_FQNS         e.g. sb-award-sandbox.servicebus.windows.net
    SERVICE_BUS_TOPIC_NAME   e.g. award-events
    MI_CLIENT_ID          Set by Terraform — disambiguates the user-assigned MI
                             when multiple identities are attached to the ACA.
                             Read automatically by DefaultAzureCredential(); no
                             explicit wiring needed in application code.
"""

import json
import logging
import os
import uuid

from azure.servicebus.aio import ServiceBusClient
from azure.servicebus import ServiceBusMessage
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .azure_credential import async_credential

logger = logging.getLogger(__name__)

_FQNS  = os.environ.get("SERVICE_BUS_FQNS", "")
_TOPIC = os.environ.get("SERVICE_BUS_TOPIC_NAME", "")


async def publish_event(
    event_type:    str,
    nomination_id: int | None = None,
    extra:         dict | None = None,
) -> None:
    """
    Publish a domain event to the Service Bus topic.

    The message body is UTF-8 encoded JSON (AMQP data section):
        b'{"event_type": "payroll.accepted", "nomination_id": 42}'

    Optional ``extra`` fields are merged into the payload.

    Raises:
        RuntimeError: if SERVICE_BUS_FQNS or SERVICE_BUS_TOPIC_NAME are not set.
    """
    if not _FQNS or not _TOPIC:
        raise RuntimeError(
            "SERVICE_BUS_FQNS and SERVICE_BUS_TOPIC_NAME must be set "
            "before calling publish_event()"
        )

    body: dict = {"event_type": event_type}
    if nomination_id is not None:
        body["nomination_id"] = nomination_id
    if extra:
        body.update(extra)
    payload = json.dumps(body).encode("utf-8")

    # Inject W3C TraceContext for distributed tracing across services.
    props: dict = {"event_type": event_type}
    TraceContextTextMapPropagator().inject(props)

    msg = ServiceBusMessage(
        payload,
        message_id=str(uuid.uuid4()),
        content_type="application/json",
        application_properties=props,
    )

    try:
        async with ServiceBusClient(_FQNS, async_credential) as client:
            async with client.get_topic_sender(_TOPIC) as sender:
                await sender.send_messages(msg)
        logger.info(
            "Published event type=%s nomination_id=%s message_id=%s",
            event_type, nomination_id, msg.message_id,
        )
    except Exception:
        logger.exception(
            "Failed to publish event type=%s nomination_id=%s",
            event_type, nomination_id,
        )
        raise
