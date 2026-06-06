"""
Handler: nomination.description-rejected

Triggered when the integrity-check pipeline rejects a nomination because its
description failed Check A (category alignment).  The nomination status is
already set to 'Rejected' by the integrity-check handler before this event
is published.

Responsibility
--------------
Send the nominator an email explaining exactly which check failed, why, and
how to resubmit with a better description.
"""

import logging

import db
import email_client

logger = logging.getLogger("auxiliary.handlers.nomination_description_rejected")


def handle(payload: dict) -> None:
    nomination_id = payload.get("nomination_id")
    check         = payload.get("check", "category_alignment")
    reason        = payload.get("reason", "The description did not meet quality requirements.")

    if not nomination_id:
        raise ValueError(f"Missing nomination_id in payload: {payload}")

    details = db.get_nomination_details(nomination_id)
    if not details:
        raise ValueError(f"Nomination {nomination_id} not found in DB")

    logger.info(
        "Sending description-rejected notification to nominator %s for nomination %d",
        details["nominator_email"], nomination_id,
    )

    body = email_client.render_description_rejected(
        nominator_name=details["nominator_name"],
        beneficiary_name=details["beneficiary_name"],
        amount=details["amount"],
        currency=details["currency"],
        check=check,
        reason=reason,
        category_description=details.get("category_description"),
    )
    email_client.send_email(
        to_email=details["nominator_email"],
        subject=(
            f"Action Required: Please Resubmit Your Nomination for "
            f"{details['beneficiary_name']}"
        ),
        body=body,
    )

    logger.info(
        "nomination.description-rejected handled for nomination %d", nomination_id
    )
