"""
Handler: nomination.approved

Triggered when an approver approves or rejects a nomination (either via the
email action button or the web UI).

Responsibility
--------------
1. Read nomination details from the DB — including the current Status, which
   is the authoritative source of the approval decision.
2. Send the appropriate outcome email to the nominator:
     Status = 'Approved' → congratulations template
     Status = 'Rejected' → polite decline template
3. Any other Status (e.g. 'Pending') indicates the event was published before
   the DB was updated — raise so the message is abandoned and retried.

Note: We do not write an additional timestamp back to Nominations for this
event. The existing ApprovedDate column (set by the API on approval) already
records when the decision was made. Email delivery is tracked in ProcessedEvents.
"""

import logging

import db
import email_client

logger = logging.getLogger("auxiliary.handlers.nomination_approved")


def handle(payload: dict) -> None:
    """
    Process a nomination.approved event.

    Args:
        payload: Decoded JSON from the Service Bus message body.
                 Expected keys: event_type, nomination_id, timestamp
    """
    nomination_id = payload.get("nomination_id")
    if not nomination_id:
        raise ValueError(f"Missing nomination_id in payload: {payload}")

    # ── 1. Read fresh data from DB ────────────────────────────────────────────
    details = db.get_nomination_details(nomination_id)
    if not details:
        raise ValueError(f"Nomination {nomination_id} not found in DB")

    status = details["status"]

    if status not in ("Approved", "Rejected"):
        # Could happen if Service Bus delivers the event before the API commits.
        # Abandoning causes a retry — by then the DB should be consistent.
        raise ValueError(
            f"Nomination {nomination_id} has unexpected status '{status}' "
            f"— expected Approved or Rejected. Will retry."
        )

    logger.info(
        "Sending nominator outcome notification",
        extra={
            "nomination_id":   nomination_id,
            "status":          status,
            "nominator_email": details["nominator_email"],
        }
    )

    # ── 2. Send outcome email to nominator ────────────────────────────────────
    if status == "Approved":
        body = email_client.render_nomination_approved(
            beneficiary_name=details["beneficiary_name"],
            dollar_amount=details["dollar_amount"],
        )
        subject = f"✅ Nomination Approved — {details['beneficiary_name']}"
    else:  # Rejected
        body = email_client.render_nomination_rejected(
            beneficiary_name=details["beneficiary_name"],
            dollar_amount=details["dollar_amount"],
        )
        subject = f"Nomination Status — {details['beneficiary_name']}"

    email_client.send_email(
        to_email=details["nominator_email"],
        subject=subject,
        body=body,
    )

    logger.info(
        "nomination.approved handled successfully",
        extra={"nomination_id": nomination_id, "status": status}
    )
