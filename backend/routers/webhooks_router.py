"""
routers/webhooks_router.py
==========================
Payment integration endpoints — Workday webhook and auxiliary-service callbacks.

Routes
------
POST  /api/webhooks/workday/payment-confirmed   — Workday payment notification
PATCH /api/nominations/{id}/payment-status      — auxiliary-service payment state update
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import utils.sqlhelper2 as sqlhelper
from utils.service_bus_publisher import publish_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

# Shared secret used to authenticate inbound calls from Workday_Proxy.
# In production this is replaced by Workday's signed webhook payload (HMAC).
# Set via Key Vault secret WORKDAY-WEBHOOK-SECRET → env var WORKDAY_WEBHOOK_SECRET.
_WORKDAY_WEBHOOK_SECRET = os.getenv("WORKDAY_WEBHOOK_SECRET", "")


# ── Models ────────────────────────────────────────────────────────────────────

class WorkdayPaymentConfirmedRequest(BaseModel):
    paymentRef:    str          # "WD-2026-00123" — correlation key
    status:        str          # "accepted" | "rejected"
    failureReason: str = ""     # populated only when status = "rejected"


class NominationPaymentStatusUpdate(BaseModel):
    status:     str       # "PaymentSubmitted" | "Paid"
    paymentRef: str = ""  # required when status = "PaymentSubmitted"


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/api/webhooks/workday/payment-confirmed", status_code=200)
async def workday_payment_confirmed(
    body: WorkdayPaymentConfirmedRequest,
    x_api_key: str = "",
):
    """
    Webhook bridge — Workday_Proxy (sandbox) or real Workday (production) POSTs
    here when a payment has been processed.

    On success: updates nomination status → Paid and publishes payout.accepted
    onto Service Bus so the auxiliary app can react (notifications etc.)
    """
    if _WORKDAY_WEBHOOK_SECRET and x_api_key != _WORKDAY_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    if body.status != "accepted":
        logger.warning(
            "Workday payment rejected: paymentRef=%s reason=%s",
            body.paymentRef, body.failureReason,
        )
        return {"detail": "rejection recorded"}

    nomination_id = sqlhelper.mark_nomination_paid_by_ref(body.paymentRef)
    if nomination_id is None:
        logger.warning(
            "workday_payment_confirmed: paymentRef=%s not found or already Paid",
            body.paymentRef,
        )
        return {"detail": "no matching PaymentSubmitted nomination"}

    logger.info(
        "Nomination %d marked Paid via paymentRef=%s", nomination_id, body.paymentRef
    )

    try:
        await publish_event(
            "payout.accepted",
            nomination_id,
            extra={"payment_ref": body.paymentRef},
        )
    except Exception:
        logger.exception(
            "Failed to publish payout.accepted for nomination %d", nomination_id
        )

    return {"detail": "payment confirmed", "nominationId": nomination_id}


@router.patch("/api/nominations/{nomination_id}/payment-status")
async def update_nomination_payment_status(
    nomination_id: int,
    body: NominationPaymentStatusUpdate,
):
    """
    Internal endpoint called by the auxiliary app after it submits a payout
    to Workday_Proxy and receives the paymentRef back.

    Not exposed through Front Door — called service-to-service only.

    Supported transitions:
      PaymentSubmitted — stores paymentRef + PaymentSubmittedAt
      Paid             — sets PayedDate (fallback; normally done via webhook)
    """
    if body.status == "PaymentSubmitted":
        if not body.paymentRef:
            raise HTTPException(
                status_code=400,
                detail="paymentRef is required when status is PaymentSubmitted",
            )
        updated = sqlhelper.mark_nomination_payment_submitted(nomination_id, body.paymentRef)
        if not updated:
            raise HTTPException(status_code=404, detail="Nomination not found")
        return {"detail": "status updated", "status": "PaymentSubmitted", "paymentRef": body.paymentRef}

    elif body.status == "Paid":
        updated = sqlhelper.mark_nomination_as_paid(nomination_id)
        if not updated:
            raise HTTPException(status_code=404, detail="Nomination not found")
        return {"detail": "status updated", "status": "Paid"}

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported payment status transition: {body.status}",
        )
