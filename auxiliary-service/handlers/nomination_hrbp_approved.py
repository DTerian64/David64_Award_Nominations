"""
Handler: nomination.hrbp-approved

Triggered when an HRBP approves a flagged nomination, transitioning it from
PendingHRBPReview → Pending so the normal manager-approval flow continues.

Responsibility
--------------
Send the nominator an informational email letting them know their nomination
has cleared HR review and is now with their manager for approval.

Note: the manager approval email (nomination.created event) is published
separately by the backend immediately after this event — the nominator email
here is just the "cleared HR" notification.
"""

import logging

import db
import email_client
import templating

logger = logging.getLogger("auxiliary.handlers.nomination_hrbp_approved")


def handle(payload: dict) -> None:
    nomination_id = payload.get("nomination_id")
    if not nomination_id:
        raise ValueError(f"Missing nomination_id in payload: {payload}")

    details = db.get_nomination_details(nomination_id)
    if not details:
        raise ValueError(f"Nomination {nomination_id} not found in DB")

    logger.info(
        "Sending HRBP-approved notification to nominator %s for nomination %d",
        details["nominator_email"], nomination_id,
    )

    lang = db.get_tenant_lang(details["tenant_id"])
    rendered = templating.render(
        details["tenant_id"], "hrbp_approved", lang,
        {
            "nominator_name":   details["nominator_name"],
            "beneficiary_name": details["beneficiary_name"],
            "formatted_amount": email_client.format_amount(details["amount"], details["currency"]),
        },
    )
    email_client.send_email(
        to_email=details["nominator_email"],
        subject=rendered["subject"],
        body=rendered["body"],
    )

    logger.info(
        "nomination.hrbp-approved handled for nomination %d", nomination_id
    )
