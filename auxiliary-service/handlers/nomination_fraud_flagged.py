"""
Handler: nomination.fraud-flagged

Triggered when a nomination is held for HRBP review because the ML model
returned a MEDIUM, HIGH, or CRITICAL fraud risk score.

Responsibility
--------------
1. Read nomination details from the DB.
2. Look up all HRBP users for the tenant.
3. Send each HRBP a review-request email containing the fraud score,
   risk level, and warning flags.
4. If no HRBPs are configured for the tenant, log a warning and continue
   (nomination remains in PendingHRBPReview; SLA job will escalate if
   no action is taken).

Failure behaviour
-----------------
Any exception propagates to the dispatcher for retry (same as all handlers).
"""

import logging

import db
import email_client

logger = logging.getLogger("auxiliary.handlers.nomination_fraud_flagged")


def handle(payload: dict) -> None:
    nomination_id = payload.get("nomination_id")
    risk_level    = payload.get("risk_level", "UNKNOWN")

    if not nomination_id:
        raise ValueError(f"Missing nomination_id in payload: {payload}")

    details = db.get_nomination_details(nomination_id)
    if not details:
        raise ValueError(f"Nomination {nomination_id} not found in DB")

    hrbp_users = db.get_hrbp_users(details["tenant_id"])
    if not hrbp_users:
        logger.warning(
            "No HRBP users configured for tenant %d — "
            "nomination %d will remain in PendingHRBPReview until SLA breach.",
            details["tenant_id"], nomination_id,
        )
        return

    fraud_flags = db.get_hrbp_fraud_flags(nomination_id)

    for hrbp in hrbp_users:
        logger.info(
            "Sending HRBP review request to %s for nomination %d (risk=%s)",
            hrbp["email"], nomination_id, risk_level,
        )
        body = email_client.render_hrbp_review_request(
            hrbp_name=hrbp["full_name"],
            nomination_id=nomination_id,
            nominator_name=details["nominator_name"],
            beneficiary_name=details["beneficiary_name"],
            amount=details["amount"],
            currency=details["currency"],
            description=details["description"],
            risk_level=risk_level,
            fraud_score=fraud_flags["fraud_score"] if fraud_flags else None,
            warning_flags=fraud_flags["warning_flags"].split(", ") if fraud_flags and fraud_flags["warning_flags"] else [],
        )
        email_client.send_email(
            to_email=hrbp["email"],
            subject=f"⚠️ HRBP Review Required — Nomination #{nomination_id} ({risk_level} Risk)",
            body=body,
        )

    logger.info(
        "nomination.fraud-flagged handled — notified %d HRBP user(s) for nomination %d",
        len(hrbp_users), nomination_id,
    )
