"""
Handler: nomination.approved  →  payout submission
====================================================
Triggered by the same nomination.approved event that also sends the nominator
email (handled by nomination_approved.py).  This handler is responsible for
the payroll side: submitting the approved nomination to Workday_Proxy and
recording the paymentRef + PaymentSubmitted status back on the nomination.

Responsibility
--------------
1. Read nomination details from the DB (beneficiaryId, amount, currency).
2. POST to Workday_Proxy /payouts with the payout details.
3. PATCH the Award API to set status = PaymentSubmitted + paymentRef.

On failure the exception propagates to the dispatcher which abandons the
message for retry (Service Bus will redeliver up to max_delivery_count times).

Note: This handler is registered under the event type "nomination.approved"
alongside the email handler.  The dispatcher calls both sequentially; they
are independent and either can fail without affecting the other.

Environment variables
---------------------
  WORKDAY_BASE_URL    Base URL of Workday_Proxy (sandbox) or real Workday (prod).
                      e.g. https://workday-proxy-sandbox.azurecontainerapps.io
  AWARD_API_BASE_URL  Base URL of the Award Nomination API (for PATCH call).
  WORKDAY_API_KEY     Shared API key sent as X-Api-Key to Workday_Proxy.
                      Not required for sandbox (proxy accepts unauthenticated calls).
"""

import logging
import os

import httpx

import db

logger = logging.getLogger("auxiliary.handlers.payout_submit")

WORKDAY_BASE_URL  = os.environ.get("WORKDAY_BASE_URL", "").rstrip("/")
AWARD_API_BASE_URL = os.environ.get("AWARD_API_BASE_URL", "").rstrip("/")
WORKDAY_API_KEY   = os.environ.get("WORKDAY_API_KEY", "")


def handle(payload: dict) -> None:
    """
    Submit a payout to Workday_Proxy for an approved nomination.

    Args:
        payload: Decoded JSON from the Service Bus message body.
                 Expected keys: event_type, nomination_id
    """
    nomination_id = payload.get("nomination_id")
    if not nomination_id:
        raise ValueError(f"Missing nomination_id in payload: {payload}")

    if not WORKDAY_BASE_URL:
        logger.warning(
            "WORKDAY_BASE_URL not configured — skipping payout submission "
            "for nomination %d",
            nomination_id,
        )
        return

    # ── 1. Fetch nomination details ───────────────────────────────────────────
    details = db.get_nomination_details(nomination_id)
    if not details:
        raise ValueError(f"Nomination {nomination_id} not found in DB")

    if details["status"] != "Approved":
        # Could be PaymentSubmitted/Paid already (duplicate event delivery) —
        # log and skip rather than double-submit.
        logger.info(
            "Nomination %d status is '%s' — payout already submitted or "
            "nomination not in Approved state. Skipping.",
            nomination_id, details["status"],
        )
        return

    # ── 2. POST to Workday_Proxy /payouts ─────────────────────────────────────
    payout_url = f"{WORKDAY_BASE_URL}/payouts"
    headers    = {"Content-Type": "application/json"}
    if WORKDAY_API_KEY:
        headers["X-Api-Key"] = WORKDAY_API_KEY

    payout_body = {
        "nominationId": nomination_id,
        "employeeId":   details["beneficiary_id"],
        "amount":       details["amount"],
        "currency":     details.get("currency", "USD"),
        "description":  details.get("description", ""),
    }

    logger.info(
        "Submitting payout: nominationId=%d employeeId=%d amount=%.2f %s",
        nomination_id, details["beneficiary_id"],
        details["amount"], details.get("currency", "USD"),
    )

    with httpx.Client(timeout=30) as client:
        response = client.post(payout_url, json=payout_body, headers=headers)

    if response.status_code != 202:
        raise RuntimeError(
            f"Workday_Proxy returned {response.status_code} for nomination "
            f"{nomination_id}: {response.text}"
        )

    payment_ref = response.json().get("paymentRef")
    if not payment_ref:
        raise RuntimeError(
            f"Workday_Proxy response missing paymentRef for nomination {nomination_id}: "
            f"{response.text}"
        )

    logger.info(
        "Payout submitted: nominationId=%d paymentRef=%s",
        nomination_id, payment_ref,
    )

    # ── 3. PATCH Award API — status = PaymentSubmitted ────────────────────────
    if not AWARD_API_BASE_URL:
        logger.warning(
            "AWARD_API_BASE_URL not configured — paymentRef %s not stored for "
            "nomination %d",
            payment_ref, nomination_id,
        )
        return

    patch_url = f"{AWARD_API_BASE_URL}/api/nominations/{nomination_id}/payment-status"
    with httpx.Client(timeout=15) as client:
        patch_resp = client.patch(
            patch_url,
            json={"status": "PaymentSubmitted", "paymentRef": payment_ref},
        )

    if patch_resp.status_code != 200:
        raise RuntimeError(
            f"Award API PATCH returned {patch_resp.status_code} for nomination "
            f"{nomination_id}: {patch_resp.text}"
        )

    logger.info(
        "Nomination %d status updated to PaymentSubmitted (paymentRef=%s)",
        nomination_id, payment_ref,
    )
