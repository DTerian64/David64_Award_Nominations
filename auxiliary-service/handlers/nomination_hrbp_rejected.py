"""
Handler: nomination.hrbp-rejected

Triggered when an HRBP rejects a flagged nomination.

Responsibility
--------------
Send the nominator a notification that their nomination did not proceed past
the HR review stage.  Includes the HRBP's reason if one was provided.

The nomination status is already set to 'Rejected' by the backend before
this event is published.
"""

import logging

import db
import email_client
import templating

logger = logging.getLogger("auxiliary.handlers.nomination_hrbp_rejected")


def handle(payload: dict) -> None:
    nomination_id = payload.get("nomination_id")
    reason        = payload.get("reason", "")

    if not nomination_id:
        raise ValueError(f"Missing nomination_id in payload: {payload}")

    details = db.get_nomination_details(nomination_id)
    if not details:
        raise ValueError(f"Nomination {nomination_id} not found in DB")

    logger.info(
        "Sending HRBP-rejected notification to nominator %s for nomination %d",
        details["nominator_email"], nomination_id,
    )

    lang = db.get_tenant_lang(details["tenant_id"])
    rendered = templating.render(
        details["tenant_id"], "hrbp_rejected", lang,
        {
            "nominator_name":   details["nominator_name"],
            "beneficiary_name": details["beneficiary_name"],
            "formatted_amount": email_client.format_amount(details["amount"], details["currency"]),
            "reason":           reason,
        },
    )
    email_client.send_email(
        to_email=details["nominator_email"],
        subject=rendered["subject"],
        body=rendered["body"],
    )

    logger.info(
        "nomination.hrbp-rejected handled for nomination %d", nomination_id
    )
