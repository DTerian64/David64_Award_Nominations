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

from utils import db
from utils import email_client
from utils import templating

logger = logging.getLogger("auxiliary.handlers.nomination_fraud_flagged")


def handle(payload: dict) -> None:
    nomination_id = payload.get("nomination_id")
    risk_level    = payload.get("risk_level", "UNKNOWN")

    if not nomination_id:
        raise ValueError(f"Missing nomination_id in payload: {payload}")

    details = db.get_nomination_details(nomination_id)
    if not details:
        raise ValueError(f"Nomination {nomination_id} not found in DB")

    hrbp_users  = db.get_hrbp_users(details["tenant_id"])
    fraud_flags = db.get_hrbp_fraud_flags(nomination_id)
    portal_url  = db.get_tenant_portal_url(details["tenant_id"])
    lang        = db.get_tenant_lang(details["tenant_id"])

    # ── No HRBP configured — attempt fallback to tenant admin ────────────────
    if not hrbp_users:
        fallback = db.get_tenant_fallback_admin(details["tenant_id"])
        if fallback:
            logger.warning(
                "No HRBP users configured — falling back to tenant admin",
                extra={
                    "nomination_id": nomination_id,
                    "tenant_id":     details["tenant_id"],
                    "fallback_email": fallback["email"],
                },
            )
            rendered = templating.render(
                details["tenant_id"], "hrbp_review_request", lang,
                {
                    "hrbp_name":        fallback["full_name"],
                    "nomination_id":    nomination_id,
                    "nominator_name":   details["nominator_name"],
                    "beneficiary_name": details["beneficiary_name"],
                    "formatted_amount": email_client.format_amount(details["amount"], details["currency"]),
                    "description":      details["description"],
                    "risk_level":       risk_level,
                    "risk_color":       email_client.risk_color(risk_level),
                    "fraud_score":      fraud_flags["fraud_score"] if fraud_flags else None,
                    "warning_flags":    fraud_flags["warning_flags"].split(", ") if fraud_flags and fraud_flags["warning_flags"] else [],
                    "portal_url":       None,
                },
            )
            # Fallback-admin path keeps its own distinct subject (no HRBP assigned).
            email_client.send_email(
                to_email=fallback["email"],
                subject=(
                    f"⚠️ [No HRBP Assigned] Fraud Review Required — "
                    f"Nomination #{nomination_id} ({risk_level} Risk)"
                ),
                body=rendered["body"],
            )
        else:
            logger.warning(
                "No HRBP users and no fallback_admin_email configured — "
                "nomination remains in PendingHRBPReview, SLA job will escalate. "
                "Fix: add 'fallback_admin_email' to the tenant Config JSON.",
                extra={
                    "nomination_id": nomination_id,
                    "tenant_id":     details["tenant_id"],
                },
            )
        return

    # ── Normal path — notify all HRBP users ──────────────────────────────────
    for hrbp in hrbp_users:
        logger.info(
            "Sending HRBP review request",
            extra={
                "nomination_id": nomination_id,
                "risk_level":    risk_level,
                "hrbp_email":    hrbp["email"],
            },
        )
        rendered = templating.render(
            details["tenant_id"], "hrbp_review_request", lang,
            {
                "hrbp_name":        hrbp["full_name"],
                "nomination_id":    nomination_id,
                "nominator_name":   details["nominator_name"],
                "beneficiary_name": details["beneficiary_name"],
                "formatted_amount": email_client.format_amount(details["amount"], details["currency"]),
                "description":      details["description"],
                "risk_level":       risk_level,
                "risk_color":       email_client.risk_color(risk_level),
                "fraud_score":      fraud_flags["fraud_score"] if fraud_flags else None,
                "warning_flags":    fraud_flags["warning_flags"].split(", ") if fraud_flags and fraud_flags["warning_flags"] else [],
                "portal_url":       portal_url,
            },
        )
        email_client.send_email(
            to_email=hrbp["email"],
            subject=rendered["subject"],
            body=rendered["body"],
        )

    logger.info(
        "nomination.fraud-flagged handled",
        extra={
            "nomination_id":    nomination_id,
            "hrbp_users_count": len(hrbp_users),
            "risk_level":       risk_level,
        },
    )
