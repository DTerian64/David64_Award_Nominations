"""
Award Auxiliary Service — Service Bus worker entry point.

Lifecycle
---------
1. Container starts (KEDA detected messages on the award-events subscription).
2. ServiceBusClient opens a receiver on the email-processor subscription.
3. Messages are processed in a continuous loop; each message is completed
   (acknowledged) on success or abandoned (returned to queue) on transient error.
4. On SIGTERM (KEDA scaling to zero), the shutdown flag is set and the loop
   exits cleanly after finishing the current message.

Message flow
------------
   Service Bus topic: award-events
   Subscription:      email-processor
   Lock duration:     5 minutes (matches Terraform config)
   Max delivery:      5 (after which the message is dead-lettered)

Event types handled (routed by dispatcher.py)
---------------------------------------------
   nomination.created  → emails the approver (approve/reject buttons)
   nomination.approved → emails the nominator with the outcome
"""

import json
import logging
import os
import signal
import sys
import time
from contextvars import ContextVar

from azure.identity import DefaultAzureCredential
from azure.monitor.opentelemetry import configure_azure_monitor
from azure.servicebus import ServiceBusClient, ServiceBusReceiveMode

from dispatcher import dispatch

# ── Message-ID context ────────────────────────────────────────────────────────
# Holds the Service Bus message_id for the message currently being processed.
# Set in the receive loop before dispatch; reset after. Propagates automatically
# to every logger in every module via _MessageIdFilter — no need to thread
# message_id through function signatures or add it to every extra={} call.
#
# Usage in the receive loop:
#   _token = _current_message_id.set(message_id)
#   try:
#       ...
#   finally:
#       _current_message_id.reset(_token)
_current_message_id: ContextVar[str] = ContextVar("message_id", default="")


# ── Logging ───────────────────────────────────────────────────────────────────
class _MessageIdFilter(logging.Filter):
    """Injects the current message_id into every LogRecord.

    This means every log line emitted while processing a Service Bus message —
    regardless of which module emits it — contains message_id in its extras.
    KQL queries can then filter by message_id across the entire processing chain:

        | where Log_s has "4dffae70-baef-451f-808c-522636bbd3d7"
    """
    def filter(self, record: logging.LogRecord) -> bool:
        # Only inject if not already explicitly set by the caller.
        if not hasattr(record, "message_id"):
            mid = _current_message_id.get()
            if mid:
                record.message_id = mid
        return True


class _ExtraFormatter(logging.Formatter):
    """Appends any extra fields as key=value pairs after the log message.

    Python's format string only renders built-in LogRecord attributes — extra
    fields are attached to the record but never printed unless a formatter
    explicitly reads them. This formatter does that automatically.
    """
    _BUILTIN = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in self._BUILTIN
        }
        if extras:
            pairs = "  ".join(f"{k}={v!r}" for k, v in sorted(extras.items()))
            return f"{base}  {pairs}"
        return base


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_ExtraFormatter(
    fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
))
logging.basicConfig(level=logging.INFO, handlers=[_handler])

# Attach the filter to the root logger so it applies to every module.
logging.getLogger().addFilter(_MessageIdFilter())

logger = logging.getLogger("auxiliary.main")

# Temporarily enable DEBUG on the db layer to trace ProcessedEvents writes.
# Remove once the idempotency behaviour is confirmed stable.
logging.getLogger("auxiliary.db").setLevel(logging.DEBUG)

from dotenv import load_dotenv
load_dotenv()

# ── Application Insights (optional — graceful if not configured) ──────────────
_ai_conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
if _ai_conn:
    configure_azure_monitor(connection_string=_ai_conn)
    logger.info("Application Insights telemetry enabled")
else:
    logger.warning("APPLICATIONINSIGHTS_CONNECTION_STRING not set — telemetry disabled")

# ── Configuration ─────────────────────────────────────────────────────────────
SERVICE_BUS_FQNS         = os.environ["SERVICE_BUS_FQNS"]           # sb-award-sandbox.servicebus.windows.net
SERVICE_BUS_TOPIC        = os.environ["SERVICE_BUS_TOPIC_NAME"]      # award-events
SERVICE_BUS_SUBSCRIPTION = os.environ["SERVICE_BUS_SUBSCRIPTION_NAME"]  # email-processor

MAX_MESSAGE_COUNT = int(os.getenv("MAX_MESSAGE_COUNT", "10"))   # messages per receive call
MAX_WAIT_TIME     = int(os.getenv("MAX_WAIT_TIME_SECONDS", "5")) # seconds to wait for messages

# ── Graceful shutdown ─────────────────────────────────────────────────────────
_shutdown_requested = False

def _handle_sigterm(signum, frame):
    global _shutdown_requested
    logger.info("SIGTERM received — finishing current batch then shutting down")
    _shutdown_requested = True

signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT,  _handle_sigterm)


def main() -> None:
    logger.info(
        "Auxiliary worker starting",
        extra={
            "service_bus_fqns": SERVICE_BUS_FQNS,
            "topic": SERVICE_BUS_TOPIC,
            "subscription": SERVICE_BUS_SUBSCRIPTION,
        }
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

            logger.info("Receiver open — waiting for messages")

            while not _shutdown_requested:
                messages = receiver.receive_messages(
                    max_message_count=MAX_MESSAGE_COUNT,
                    max_wait_time=MAX_WAIT_TIME,
                )

                if not messages:
                    # No messages in this window — loop back and wait again.
                    # KEDA will eventually scale the container to zero when the
                    # subscription stays empty.
                    continue

                for message in messages:
                    if _shutdown_requested:
                        # Abandon so the message is retried by the next container.
                        receiver.abandon_message(message)
                        continue

                    message_id = str(message.message_id)

                    # Bind message_id into the logging context for the entire
                    # duration of this message's processing. _MessageIdFilter
                    # injects it into every LogRecord emitted by any module
                    # (db, dispatcher, handlers) — no need to pass it through
                    # function args or add it to every extra={} call.
                    _mid_token = _current_message_id.set(message_id)
                    try:
                        # Reassemble body — handle both AMQP encodings:
                        #   data section  (bytes) → publisher used .encode("utf-8")
                        #   value section (str)   → legacy messages published as plain str
                        raw_body = message.body
                        try:
                            chunks = list(raw_body)
                            if chunks and isinstance(chunks[0], (bytes, bytearray)):
                                body_str = b"".join(chunks).decode("utf-8")
                            else:
                                body_str = "".join(str(c) for c in chunks)
                        except Exception as body_exc:
                            body_str = str(raw_body)
                            logger.warning(
                                "Could not reassemble message body cleanly — fell back to str()",
                                extra={"error": str(body_exc)},
                            )

                        logger.info(
                            "Message received",
                            extra={"body": body_str[:200]},
                        )

                        try:
                            payload = json.loads(body_str)
                        except json.JSONDecodeError as exc:
                            logger.error(
                                "Invalid JSON in message body — dead-lettering",
                                extra={"error": str(exc)},
                            )
                            receiver.dead_letter_message(
                                message,
                                reason="InvalidJson",
                                error_description=str(exc),
                            )
                            continue

                        try:
                            result = dispatch(message_id, payload)
                            receiver.complete_message(message)
                            logger.info(
                                "Message completed",
                                extra={"result": result},
                            )
                        except Exception as exc:
                            logger.error(
                                "Message processing failed — abandoning for retry",
                                extra={"error": str(exc)},
                                exc_info=True,
                            )
                            # Abandon returns the message to the queue.
                            # After max_delivery_count attempts it goes to the DLQ.
                            receiver.abandon_message(message)

                    finally:
                        # Always clear the message_id context, even on exception,
                        # so the next message starts with a clean logging context.
                        _current_message_id.reset(_mid_token)

    logger.info("Auxiliary worker shut down cleanly")


if __name__ == "__main__":
    main()
