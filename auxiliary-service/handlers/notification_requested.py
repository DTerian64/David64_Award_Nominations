"""
Handler: notification.requested

Triggered when the Ask Analytics agent (or any other backend service) wants
to send a free-form email — typically a fraud investigation summary, an alert,
or a report delivered to a colleague.

Published by: backend/agents/skills/notifications/tools.py → send_email()

Payload shape
─────────────
{
    "event_type": "notification.requested",
    "from":       "system@terian-services.com",   # optional — defaults to FROM_EMAIL
    "to":         "compliance@company.com",        # required
    "subject":    "Fraud Investigation Summary",   # required
    "body":       "Plain-text body..."             # required
}

Responsibility
──────────────
Validate the required fields, then deliver via email_client.send_plain().
No DB lookup needed — all content is in the payload.
"""

import logging

import email_client

logger = logging.getLogger("auxiliary.handlers.notification_requested")

_REQUIRED = ("to", "subject", "body")


def handle(payload: dict) -> None:
    """
    Process a notification.requested event.

    Args:
        payload: Decoded JSON from the Service Bus message body.
    """
    missing = [f for f in _REQUIRED if not payload.get(f)]
    if missing:
        raise ValueError(
            f"notification.requested payload missing required fields: {missing}. "
            f"Got: {list(payload.keys())}"
        )

    to      = payload["to"]
    subject = payload["subject"]
    body    = payload["body"]
    from_   = payload.get("from")   # optional — email_client defaults to FROM_EMAIL

    logger.info(
        "Delivering notification email",
        extra={"to": to, "subject": subject, "from_override": from_},
    )

    email_client.send_plain(
        to_email      = to,
        subject       = subject,
        body          = body,
        from_override = from_,
    )

    logger.info(
        "notification.requested handled successfully",
        extra={"to": to, "subject": subject},
    )
