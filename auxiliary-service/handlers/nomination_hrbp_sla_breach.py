"""
Handler: nomination.hrbp-sla-breach

Triggered by the Logic App la-award-hrbp-sla-{env} when it calls the backend's
POST /api/internal/checkPendingHRBPReview and the backend finds nominations
that have been in PendingHRBPReview longer than HRBP_SLA_HOURS.

Responsibility
--------------
Email all HRBP users (and optionally AWard_Nomination_Admins) for the affected
tenant to alert them that a nomination has breached its review SLA.

One event is published per breached nomination so each gets its own email
thread and idempotency key in dbo.ProcessedEvents.
"""

import logging

import db
import email_client

logger = logging.getLogger("auxiliary.handlers.nomination_hrbp_sla_breach")


def handle(payload: dict) -> None:
    nomination_id   = payload.get("nomination_id")
    tenant_id       = payload.get("tenant_id")
    risk_level      = payload.get("risk_level", "UNKNOWN")
    nomination_date = payload.get("nomination_date", "unknown")
    sla_hours       = payload.get("sla_hours", 72)

    if not nomination_id:
        raise ValueError(f"Missing nomination_id in payload: {payload}")

    details = db.get_nomination_details(nomination_id)
    if not details:
        raise ValueError(f"Nomination {nomination_id} not found in DB")

    # Re-check the nomination is still pending — an HRBP may have acted
    # between the Logic App firing and this handler running.
    if details["status"] != "PendingHRBPReview":
        logger.info(
            "Nomination %d is no longer in PendingHRBPReview (status=%s) — skipping SLA breach email.",
            nomination_id, details["status"],
        )
        return

    effective_tenant_id = tenant_id or details["tenant_id"]
    hrbp_users = db.get_hrbp_users(effective_tenant_id)

    recipients = hrbp_users if hrbp_users else []
    if not recipients:
        logger.warning(
            "No HRBP users for tenant %d — SLA breach for nomination %d has no recipients.",
            effective_tenant_id, nomination_id,
        )
        return

    for recipient in recipients:
        logger.info(
            "Sending SLA breach alert to %s for nomination %d",
            recipient["email"], nomination_id,
        )
        body = email_client.render_hrbp_sla_breach(
            recipient_name=recipient["full_name"],
            nomination_id=nomination_id,
            nominator_name=details["nominator_name"],
            beneficiary_name=details["beneficiary_name"],
            risk_level=risk_level,
            nomination_date=nomination_date,
            sla_hours=sla_hours,
        )
        email_client.send_email(
            to_email=recipient["email"],
            subject=f"🚨 SLA Breach — Nomination #{nomination_id} Awaiting HRBP Review",
            body=body,
        )

    logger.info(
        "nomination.hrbp-sla-breach handled — alerted %d recipient(s) for nomination %d",
        len(recipients), nomination_id,
    )
