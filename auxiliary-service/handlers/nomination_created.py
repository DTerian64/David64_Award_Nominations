"""
Handler: nomination.created

Triggered when a new nomination is saved to the database.

Responsibility
--------------
1. Read nomination details from the DB (fresh, authoritative source).
2. Generate time-limited approve / reject URLs with signed JWT tokens
   (same logic as backend/token_utils.py).
3. Send the approver notification email with action buttons.
4. Stamp ApproverNotifiedAt on the Nominations row (business lifecycle data).

Failure behaviour
-----------------
Any exception propagates to the dispatcher, which records 'error' in
ProcessedEvents and re-raises so main.py abandons the message for retry.
Service Bus will redeliver up to max_delivery_count (5) times before
dead-lettering. The idempotency check in the dispatcher prevents duplicate
emails on redelivery IF the claim_message insert succeeded — i.e. emails
are sent at-most-once after the first successful claim.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import jwt

import db
import email_client

logger = logging.getLogger("auxiliary.handlers.nomination_created")

# ── Token config — mirrors backend/token_utils.py ─────────────────────────────
_SECRET_KEY   = os.environ["EMAIL_ACTION_SECRET_KEY"]
_EXPIRY_HOURS = int(os.getenv("EMAIL_ACTION_TOKEN_EXPIRY_HOURS", "72"))
_API_BASE_URL = os.environ["API_BASE_URL"]


def _make_action_url(nomination_id: int, action: str, approver_id: int) -> str:
    """Generate a signed JWT action URL for the approver email button."""
    now = datetime.now(timezone.utc)
    payload = {
        "nomination_id": nomination_id,
        "action":        action,          # "approve" | "reject"
        "approver_id":   approver_id,
        "iat":           now,
        "exp":           now + timedelta(hours=_EXPIRY_HOURS),
    }
    token = jwt.encode(payload, _SECRET_KEY, algorithm="HS256")
    return f"{_API_BASE_URL}/api/nominations/email-action?token={token}"


def handle(payload: dict) -> None:
    """
    Process a nomination.created event.

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

    logger.info(
        "Sending approver notification",
        extra={
            "nomination_id":  nomination_id,
            "approver_email": details["approver_email"],
            "amount":         details["amount"],
            "currency":       details["currency"],
        }
    )

    # ── 2. Generate approve / reject URLs ─────────────────────────────────────
    approve_url = _make_action_url(nomination_id, "approve", details["approver_id"])
    reject_url  = _make_action_url(nomination_id, "reject",  details["approver_id"])

    # ── 3. Send email to approver ─────────────────────────────────────────────
    body = email_client.render_nomination_pending(
        manager_name=details["approver_name"],
        nominator_name=details["nominator_name"],
        beneficiary_name=details["beneficiary_name"],
        dollar_amount=details["amount"],
        currency=details["currency"],
        description=details["description"],
        approve_url=approve_url,
        reject_url=reject_url,
    )

    email_client.send_email(
        to_email=details["approver_email"],
        subject=f"Award Nomination Pending Approval — {details['beneficiary_name']}",
        body=body,
    )

    # ── 4. Stamp ApproverNotifiedAt (business lifecycle) ─────────────────────
    db.set_approver_notified(nomination_id)

    logger.info(
        "nomination.created handled successfully",
        extra={"nomination_id": nomination_id}
    )
