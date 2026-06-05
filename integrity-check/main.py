"""
award-integrity-check — Service Bus fraud detection worker
==========================================================
Consumes nomination.submitted events from the fraud-processor subscription,
runs the full fraud assessment pipeline (fraud_check.py), and re-publishes
the routing outcome (nomination.created or nomination.fraud-flagged) back to
the award-events topic for the auxiliary service to pick up.

Lifecycle
---------
1. KEDA detects messages on the fraud-processor subscription.
2. ServiceBusClient opens a receiver; messages are processed in a loop.
3. Each message is passed to handle_nomination_submitted().
4. On success → complete. On transient error → abandon (requeue for retry).
5. On SIGTERM → shutdown flag set; loop exits after current message.

Idempotency
-----------
Handled by dbo.ProcessedEvents (same pattern as auxiliary service):
insert before processing → PK violation = already done → skip.
"""

import json
import logging
import os
import signal
import sys
import time
from contextlib import suppress
from contextvars import ContextVar
from datetime import datetime, timezone

from azure.identity import DefaultAzureCredential
from azure.monitor.opentelemetry import configure_azure_monitor
from azure.servicebus import ServiceBusClient, ServiceBusReceiveMode
from dotenv import load_dotenv
from opentelemetry import context as otel_context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from handler import handle
from logging_config import setup_logging

load_dotenv()

# ── Message-ID context ────────────────────────────────────────────────────────
# Holds the Service Bus message_id for the message currently being processed.
# Propagates automatically to every logger via _MessageIdFilter.
_current_message_id: ContextVar[str] = ContextVar("message_id", default="")


# ── Logging ───────────────────────────────────────────────────────────────────
class _MessageIdFilter(logging.Filter):
    """Injects the current Service Bus message_id into every LogRecord.

    Combined with setup_logging()'s _ExtrasToMessageFilter, the message_id
    is merged into the JSON message body — searchable in KQL as:
        | where Log_s has "4dffae70-baef-451f-808c-522636bbd3d7"
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "message_id"):
            mid = _current_message_id.get()
            if mid:
                record.message_id = mid
        return True


# setup_logging() installs JSONFormatter + App_Log prefix + extras-to-message.
# We then attach _MessageIdFilter so every log line also carries message_id.
setup_logging()
for _h in logging.getLogger().handlers:
    _h.addFilter(_MessageIdFilter())

logger = logging.getLogger("integrity_check.main")

# ── Application Insights ──────────────────────────────────────────────────────
_ai_conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
if _ai_conn:
    configure_azure_monitor(connection_string=_ai_conn)
    logger.info("Application Insights telemetry enabled")

# ── Configuration ─────────────────────────────────────────────────────────────
SERVICE_BUS_FQNS         = os.environ["SERVICE_BUS_FQNS"]
SERVICE_BUS_TOPIC        = os.environ["SERVICE_BUS_TOPIC_NAME"]
SERVICE_BUS_SUBSCRIPTION = os.environ["SERVICE_BUS_SUBSCRIPTION_NAME"]   # fraud-processor

MAX_MESSAGE_COUNT = int(os.getenv("MAX_MESSAGE_COUNT", "5"))
MAX_WAIT_TIME     = int(os.getenv("MAX_WAIT_TIME_SECONDS", "5"))

# ── Graceful shutdown ─────────────────────────────────────────────────────────
_shutdown = False

def _handle_sigterm(signum, frame):
    global _shutdown
    logger.info("SIGTERM received — finishing current message then shutting down")
    _shutdown = True

signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT,  _handle_sigterm)


def main() -> None:
    logger.info(
        "Integrity check worker starting",
        extra={
            "service_bus_fqns":    SERVICE_BUS_FQNS,
            "topic":               SERVICE_BUS_TOPIC,
            "subscription":        SERVICE_BUS_SUBSCRIPTION,
        },
    )

    credential = DefaultAzureCredential()

    with ServiceBusClient(
        fully_qualified_namespace=SERVICE_BUS_FQNS,
        credential=credential,
        logging_enable=False,
    ) as client:
        with client.get_subscription_receiver(
            topic_name=SERVICE_BUS_TOPIC,
            subscription_name=SERVICE_BUS_SUBSCRIPTION,
            receive_mode=ServiceBusReceiveMode.PEEK_LOCK,
            max_wait_time=MAX_WAIT_TIME,
        ) as receiver:

            logger.info("Receiver open — waiting for nomination.submitted messages")

            while not _shutdown:
                messages = receiver.receive_messages(
                    max_message_count=MAX_MESSAGE_COUNT,
                    max_wait_time=MAX_WAIT_TIME,
                )
                if not messages:
                    continue

                for message in messages:
                    if _shutdown:
                        receiver.abandon_message(message)
                        continue

                    message_id = str(message.message_id)
                    _mid_token = _current_message_id.set(message_id)

                    try:
                        # Decode body (AMQP data section → bytes)
                        raw = message.body
                        try:
                            chunks = list(raw)
                            body_str = (
                                b"".join(chunks).decode("utf-8")
                                if chunks and isinstance(chunks[0], (bytes, bytearray))
                                else "".join(str(c) for c in chunks)
                            )
                        except Exception as body_exc:
                            body_str = str(raw)
                            logger.warning("Body decode fallback", extra={"error": str(body_exc)})

                        logger.info("Message received", extra={"body": body_str[:200]})

                        try:
                            payload = json.loads(body_str)
                        except json.JSONDecodeError as exc:
                            logger.error("Invalid JSON — dead-lettering",
                                         extra={"error": str(exc)})
                            receiver.dead_letter_message(
                                message,
                                reason="InvalidJson",
                                error_description=str(exc),
                            )
                            continue

                        logger.info(
                            "nomination.submitted received",
                            extra={
                                "nomination_id": payload.get("nomination_id"),
                                "event_type":    payload.get("event_type"),
                            },
                        )

                        # Restore the OTel trace context published by the
                        # backend so all spans emitted during handle() are
                        # linked as children of the originating HTTP request.
                        carrier = {
                            k: v for k, v in
                            (message.application_properties or {}).items()
                            if isinstance(k, str)
                        }
                        parent_ctx = TraceContextTextMapPropagator().extract(carrier)
                        otel_token = otel_context.attach(parent_ctx)

                        try:
                            handle(message_id, payload)
                            receiver.complete_message(message)
                            logger.info(
                                "Message completed",
                                extra={
                                    "nomination_id": payload.get("nomination_id"),
                                    "event_type":    payload.get("event_type"),
                                },
                            )
                        except Exception as exc:
                            logger.error(
                                "Message processing failed — abandoning for retry",
                                extra={"error": str(exc)},
                                exc_info=True,
                            )
                            receiver.abandon_message(message)
                        finally:
                            otel_context.detach(otel_token)

                    finally:
                        _current_message_id.reset(_mid_token)

    logger.info("Integrity check worker shut down cleanly")


if __name__ == "__main__":
    main()
