"""
Handler: notification.access_requested

Triggered when a visitor requests demo access via the self-registration form
at demo-awards.terian-services.com/demo/request.

Published by: backend/demo_router.py → POST /api/demo/request

Payload shape
─────────────
{
    "event_type":  "notification.access_requested",
    "to":          "visitor@company.com",    # required — requestor's email
    "first_name":  "Jane",                   # required — used in email greeting
    "redeem_url":  "https://invitations.microsoft.com/redeem?..."  # required
}

Responsibility
──────────────
Validate required fields, render the branded HTML invitation email, and
deliver it via email_client.send_email().
No DB lookup needed — all content is in the payload.
"""

import logging

import email_client

logger = logging.getLogger("auxiliary.handlers.access_requested")

_REQUIRED = ("to", "first_name", "redeem_url")


def handle(payload: dict) -> None:
    missing = [f for f in _REQUIRED if not payload.get(f)]
    if missing:
        raise ValueError(
            f"notification.access_requested payload missing required fields: {missing}. "
            f"Got: {list(payload.keys())}"
        )

    to         = payload["to"]
    first_name = payload["first_name"]
    redeem_url = payload["redeem_url"]

    logger.info(
        "Delivering demo access invitation",
        extra={"to": to},
    )

    email_client.send_email(
        to_email = to,
        subject  = "Your Award Nominations demo access is ready",
        body     = email_client.render_demo_access_invite(first_name, redeem_url),
    )

    logger.info(
        "notification.access_requested handled successfully",
        extra={"to": to},
    )
