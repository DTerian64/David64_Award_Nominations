"""
Handler: payout.accepted
========================
Triggered when the Award API webhook bridge receives a PayoutAccepted
confirmation from Workday_Proxy (or real Workday) and publishes this event
onto the Service Bus.

At this point the nomination is already marked Paid in SQL (the webhook
bridge does that synchronously before publishing the event).  This handler
is responsible for any downstream reactions:
  - Sending a payment confirmation email to the nominator
  - Any future notifications (HR system sync, Slack, etc.)

Responsibility
--------------
1. Read fresh nomination details — confirm status is Paid.
2. Send payment confirmation email to the nominator.

If the nomination is not yet Paid (race between webhook and event delivery),
the handler raises so the message is retried.
"""

import logging

import db
import email_client
import templating

logger = logging.getLogger("auxiliary.handlers.payout_accepted")


def handle(payload: dict) -> None:
    """
    Process a payout.accepted event.

    Args:
        payload: Decoded JSON from the Service Bus message body.
                 Expected keys: event_type, nomination_id, payment_ref
    """
    nomination_id = payload.get("nomination_id")
    payment_ref   = payload.get("payment_ref", "")

    if not nomination_id:
        raise ValueError(f"Missing nomination_id in payload: {payload}")

    # ── 1. Read fresh data ────────────────────────────────────────────────────
    details = db.get_nomination_details(nomination_id)
    if not details:
        raise ValueError(f"Nomination {nomination_id} not found in DB")

    if details["status"] != "Paid":
        # Webhook bridge should have set status before publishing — if not,
        # abandon and retry to wait for the DB to catch up.
        raise ValueError(
            f"Nomination {nomination_id} status is '{details['status']}' "
            f"— expected Paid. Will retry."
        )

    logger.info(
        "Sending payment confirmation email",
        extra={
            "nomination_id":   nomination_id,
            "payment_ref":     payment_ref,
            "nominator_email": details["nominator_email"],
        },
    )

    # ── 2. Send payment confirmation email ────────────────────────────────────
    lang = db.get_tenant_lang(details["tenant_id"])
    rendered = templating.render(
        details["tenant_id"], "payment_confirmed", lang,
        {
            "beneficiary_name": details["beneficiary_name"],
            "formatted_amount": email_client.format_amount(details["amount"], details.get("currency", "USD")),
            "payment_ref":      payment_ref,
        },
    )

    email_client.send_email(
        to_email=details["nominator_email"],
        subject=rendered["subject"],
        body=rendered["body"],
    )

    logger.info(
        "payout.accepted handled successfully",
        extra={"nomination_id": nomination_id, "payment_ref": payment_ref},
    )
