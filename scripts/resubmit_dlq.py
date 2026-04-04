"""
Resubmit dead-letter messages from the email-processor subscription back to award-events topic.

Reads all messages from the DLQ, republishes each to the topic with a fresh message_id,
then completes (removes) the DLQ message. Safe to run multiple times — already-empty
DLQ is a no-op.

Usage:
    python scripts/resubmit_dlq.py [--dry-run]

Authentication:
    Uses DefaultAzureCredential — run `az login` first if not already authenticated.

Environment variables (optional — defaults match sandbox):
    SERVICE_BUS_FQNS          e.g. sb-award-sandbox.servicebus.windows.net
    SERVICE_BUS_TOPIC_NAME    e.g. award-events
    SERVICE_BUS_SUBSCRIPTION_NAME  e.g. email-processor
"""

import asyncio
import json
import os
import sys
import uuid
import argparse
import logging

from azure.servicebus.aio import ServiceBusClient
from azure.servicebus import ServiceBusMessage
from azure.identity.aio import DefaultAzureCredential

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

FQNS         = os.environ.get("SERVICE_BUS_FQNS",              "sb-award-sandbox.servicebus.windows.net")
TOPIC        = os.environ.get("SERVICE_BUS_TOPIC_NAME",        "award-events")
SUBSCRIPTION = os.environ.get("SERVICE_BUS_SUBSCRIPTION_NAME", "email-processor")


async def resubmit(dry_run: bool, purge: bool = False) -> None:
    credential = DefaultAzureCredential()

    async with ServiceBusClient(FQNS, credential) as client:
        # Dead-letter receiver for the subscription
        dlq_receiver = client.get_subscription_receiver(
            topic_name=TOPIC,
            subscription_name=SUBSCRIPTION,
            sub_queue="deadletter",
        )
        # Sender back to the topic
        sender = client.get_topic_sender(topic_name=TOPIC)

        async with dlq_receiver, sender:
            messages = await dlq_receiver.receive_messages(max_message_count=100, max_wait_time=5)

            if not messages:
                logger.info("Dead-letter queue is empty — nothing to resubmit.")
                return

            logger.info("Found %d message(s) in DLQ.", len(messages))

            for dlq_msg in messages:
                # Body may be an AMQP data section (bytes) or value section (str),
                # depending on how the publisher constructed the ServiceBusMessage.
                raw = dlq_msg.body
                if isinstance(raw, (bytes, bytearray)):
                    body_bytes = raw
                else:
                    try:
                        body_bytes = b"".join(raw)
                    except TypeError:
                        # AMQP value section — body is a plain str
                        body_bytes = str(raw).encode("utf-8")
                body_str = body_bytes.decode("utf-8")

                # Log the original message details
                original_id      = dlq_msg.message_id
                dead_letter_reason = dlq_msg.dead_letter_reason
                dead_letter_desc   = dlq_msg.dead_letter_error_description
                logger.info(
                    "DLQ message  id=%s  reason=%r  description=%r  body=%s",
                    original_id, dead_letter_reason, dead_letter_desc, body_str,
                )

                if dry_run:
                    logger.info("[DRY RUN] Would %s — skipping.", "purge" if purge else "resubmit")
                    # Abandon so the message stays in the DLQ
                    await dlq_receiver.abandon_message(dlq_msg)
                    continue

                if purge:
                    # Complete without republishing — permanently discards the message.
                    await dlq_receiver.complete_message(dlq_msg)
                    logger.info("Purged DLQ message  id=%s", original_id)
                    continue

                # Reconstruct the message preserving application_properties and content_type
                new_msg = ServiceBusMessage(
                    body_bytes,
                    message_id=str(uuid.uuid4()),
                    content_type=dlq_msg.content_type or "application/json",
                    application_properties=dict(dlq_msg.application_properties or {}),
                )

                await sender.send_messages(new_msg)
                logger.info("Resubmitted  original_id=%s  new_id=%s", original_id, new_msg.message_id)

                # Complete (remove) the DLQ message now that it has been resubmitted
                await dlq_receiver.complete_message(dlq_msg)
                logger.info("Completed DLQ message  id=%s", original_id)

    await credential.close()
    if not dry_run:
        logger.info("Done — all DLQ messages %s.", "purged" if purge else "resubmitted")


def main() -> None:
    parser = argparse.ArgumentParser(description="Resubmit or purge Service Bus dead-letter messages.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Peek at DLQ contents and log them without resubmitting, purging, or completing.",
    )
    parser.add_argument(
        "--purge", action="store_true",
        help="Complete (permanently discard) DLQ messages without republishing them to the topic.",
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.info("DRY RUN — messages will be logged but not modified.")
    if args.purge and not args.dry_run:
        logger.warning("PURGE mode — DLQ messages will be permanently discarded without resubmitting.")

    asyncio.run(resubmit(dry_run=args.dry_run, purge=args.purge))


if __name__ == "__main__":
    main()
