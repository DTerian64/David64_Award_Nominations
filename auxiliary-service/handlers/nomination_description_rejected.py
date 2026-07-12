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

from utils import db
from utils import email_client
from utils import templating

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

    check_label = {
        "category_alignment": "Description does not match category",
    }.get(check, "Description quality check failed")
    lang = db.get_tenant_lang(details["tenant_id"])
    rendered = templating.render(
        details["tenant_id"], "description_rejected", lang,
        {
            "nominator_name":         details["nominator_name"],
            "beneficiary_name":       details["beneficiary_name"],
            "formatted_amount":       email_client.format_amount(details["amount"], details["currency"]),
            "check_label":            check_label,
            "reason":                 reason,
            "category_description":   details.get("category_description"),
            "nomination_description": details.get("description"),
        },
    )
    email_client.send_email(
        to_email=details["nominator_email"],
        subject=rendered["subject"],
        body=rendered["body"],
    )

    logger.info(
        "nomination.description-rejected handled for nomination %d", nomination_id
    )
