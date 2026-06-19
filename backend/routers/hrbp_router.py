"""
routers/hrbp_router.py
======================
HRBP (HR Business Partner) review workflow endpoints.

All routes require the caller's effective user to hold the 'HRBP' role in
dbo.UserRoles.  Impersonation is supported — an admin impersonating an HRBP
user gains HRBP access for the duration of the session.

Routes
------
GET  /api/hrbp/queue
    Return all nominations in PendingHRBPReview for the tenant.

POST /api/hrbp/nominations/{id}/approve
    Approve a flagged nomination → transitions to Pending (manager flow continues).

POST /api/hrbp/nominations/{id}/reject
    Reject a flagged nomination → transitions to Rejected, nominator notified.

GET  /api/hrbp/nominations/{id}/pair-history
    Return all prior nominations between the same nominator → beneficiary pair.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

import utils.sqlhelper2 as sqlhelper
from auth import get_current_user_with_impersonation
from utils.service_bus_publisher import publish_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hrbp", tags=["hrbp"])


# ── Dependency ────────────────────────────────────────────────────────────────

def require_hrbp_role(
    current_user: dict = Depends(get_current_user_with_impersonation),
) -> dict:
    """
    FastAPI dependency — resolves the effective user and checks that they hold
    the 'HRBP' role in dbo.UserRoles.  Raises 403 if absent.
    """
    effective_user = current_user["effective_user"]
    roles = sqlhelper.get_user_roles(effective_user["UserId"])
    if "HRBP" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="HRBP role required",
        )
    return current_user


# ── Models ────────────────────────────────────────────────────────────────────

class HRBPDecisionRequest(BaseModel):
    reason: str = ""   # required for rejection, optional for approval


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/queue")
async def get_hrbp_queue(user_context: dict = Depends(require_hrbp_role)):
    """
    Return all nominations in PendingHRBPReview for the caller's tenant,
    with full fraud-flag detail.  HRBP role required.
    """
    tenant_id = user_context["effective_user"]["TenantId"]
    return sqlhelper.get_hrbp_queue(tenant_id)


@router.post("/nominations/{nomination_id}/approve")
async def hrbp_approve(
    nomination_id: int,
    body: HRBPDecisionRequest,
    user_context: dict = Depends(require_hrbp_role),
):
    """
    HRBP approves a flagged nomination → transitions to Pending so the normal
    manager-approval flow continues.  HRBP role required.
    """
    effective_user = user_context["effective_user"]
    details = sqlhelper.get_nomination_details_for_hrbp(nomination_id)
    if not details:
        raise HTTPException(status_code=404, detail="Nomination not found")
    if details["status"] != "PendingHRBPReview":
        raise HTTPException(
            status_code=400,
            detail=f"Nomination is not in PendingHRBPReview (current: {details['status']})",
        )
    if details["tenant_id"] != effective_user["TenantId"]:
        raise HTTPException(status_code=403, detail="Cross-tenant access denied")

    sqlhelper.set_nomination_status(nomination_id, "Pending")
    logger.info(
        "HRBP approved nomination %d (reviewer=%d)",
        nomination_id, effective_user["UserId"],
    )

    # Write confirmed-legitimate label → feeds next RF retrain
    try:
        sqlhelper.upsert_p2p_fraud_label(
            nomination_id=nomination_id,
            is_fraud=False,
            confirmed_by=f"HRBP:{effective_user['UserId']}",
        )
    except Exception as e:
        logger.warning("upsert_p2p_fraud_label failed for nomination %d (approve): %s", nomination_id, e)

    try:
        await publish_event(
            "nomination.hrbp-approved",
            nomination_id,
            extra={"reviewer_id": effective_user["UserId"]},
        )
        # Also fire nomination.created so the manager gets their approval email.
        await publish_event("nomination.created", nomination_id)
    except Exception as e:
        logger.warning("Failed to publish hrbp-approved events for %d: %s", nomination_id, e)

    return {"status": "approved", "nomination_id": nomination_id}


@router.post("/nominations/{nomination_id}/reject")
async def hrbp_reject(
    nomination_id: int,
    body: HRBPDecisionRequest,
    user_context: dict = Depends(require_hrbp_role),
):
    """
    HRBP rejects a flagged nomination → transitions to Rejected.
    A reason is strongly recommended and will be included in the nominator email.
    HRBP role required.
    """
    effective_user = user_context["effective_user"]
    details = sqlhelper.get_nomination_details_for_hrbp(nomination_id)
    if not details:
        raise HTTPException(status_code=404, detail="Nomination not found")
    if details["status"] != "PendingHRBPReview":
        raise HTTPException(
            status_code=400,
            detail=f"Nomination is not in PendingHRBPReview (current: {details['status']})",
        )
    if details["tenant_id"] != effective_user["TenantId"]:
        raise HTTPException(status_code=403, detail="Cross-tenant access denied")

    sqlhelper.reject_nomination(nomination_id, reason=body.reason, actor="HRBP Review")
    logger.info(
        "HRBP rejected nomination %d (reviewer=%d reason=%r)",
        nomination_id, effective_user["UserId"], body.reason,
    )

    # Write confirmed-fraud label → feeds next RF retrain as CRITICAL
    try:
        sqlhelper.upsert_p2p_fraud_label(
            nomination_id=nomination_id,
            is_fraud=True,
            confirmed_by=f"HRBP:{effective_user['UserId']}",
        )
    except Exception as e:
        logger.warning("upsert_p2p_fraud_label failed for nomination %d (reject): %s", nomination_id, e)

    try:
        await publish_event(
            "nomination.hrbp-rejected",
            nomination_id,
            extra={
                "reviewer_id": effective_user["UserId"],
                "reason":      body.reason,
            },
        )
    except Exception as e:
        logger.warning("Failed to publish hrbp-rejected event for %d: %s", nomination_id, e)

    return {"status": "rejected", "nomination_id": nomination_id}


@router.get("/nominations/{nomination_id}/pair-history")
async def get_pair_history(
    nomination_id: int,
    user_context: dict = Depends(require_hrbp_role),
):
    """
    Return all previous nominations between the same nominator → beneficiary
    pair, excluding the currently-reviewed nomination.
    Gives the HRBP reviewer the full relationship history to inform their decision.
    """
    tenant_id = user_context["effective_user"]["TenantId"]
    details   = sqlhelper.get_nomination_details_for_hrbp(nomination_id)
    if not details:
        raise HTTPException(status_code=404, detail="Nomination not found")
    if details["tenant_id"] != tenant_id:
        raise HTTPException(status_code=403, detail="Cross-tenant access denied")

    history = sqlhelper.get_pair_nomination_history(
        nominator_id=details["nominator_id"],
        beneficiary_id=details["beneficiary_id"],
        tenant_id=tenant_id,
        exclude_nomination_id=nomination_id,
    )
    return {
        "nominator_name":   details["nominator_name"],
        "beneficiary_name": details["beneficiary_name"],
        "pair_count":       len(history),   # both directions, excl. current nomination
        "history":          history,        # each row has nominator_name + beneficiary_name
    }
