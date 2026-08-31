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

POST /api/hrbp/nominations/{id}/decision
    Record a scope-aware HRBP outcome and its training disposition.

GET  /api/hrbp/nominations/{id}/pair-history
    Return all prior nominations between the same nominator → beneficiary pair.
"""

import logging
from typing import Literal

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
    outcome: Literal[
        "CLEARED_NO_CONCERN",
        "CLEARED_UNSUBSTANTIATED",
        "CONFIRMED_CONCERN",
        "CONFIRMED_SEMANTIC_CONCERN",
    ]
    reason: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/queue")
async def get_hrbp_queue(user_context: dict = Depends(require_hrbp_role)):
    """
    Return all nominations in PendingHRBPReview for the caller's tenant,
    with full fraud-flag detail.  HRBP role required.
    """
    tenant_id = user_context["effective_user"]["TenantId"]
    return sqlhelper.get_hrbp_queue(tenant_id)


@router.post("/nominations/{nomination_id}/decision")
async def hrbp_decide(
    nomination_id: int,
    body: HRBPDecisionRequest,
    user_context: dict = Depends(require_hrbp_role),
):
    """Record a model-neutral HRBP adjudication and advance the workflow."""
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
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=400, detail="Decision reason is required")

    reviewer = f"HRBP:{effective_user['UserId']}"
    try:
        result = sqlhelper.apply_hrbp_adjudication(
            nomination_id=nomination_id,
            outcome=body.outcome,
            reviewed_by=reviewer,
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result["applied"]:
        raise HTTPException(
            status_code=409,
            detail=f"HRBP decision was not applied: {result['reason']}",
        )

    logger.info(
        "HRBP adjudication recorded",
        extra={
            "nomination_id": nomination_id,
            "tenant_id": effective_user["TenantId"],
            "reviewer_id": effective_user["UserId"],
            "human_review_outcome": result["outcome"],
            "training_disposition": result["training_disposition"],
            "new_status": result["status"],
            "reason": body.reason.strip(),
        },
    )

    try:
        event_extra = {
            "reviewer_id": effective_user["UserId"],
            "outcome": result["outcome"],
            "training_disposition": result["training_disposition"],
            "reason": body.reason.strip(),
        }
        if result["status"] == "Rejected":
            await publish_event(
                "nomination.hrbp-rejected", nomination_id, extra=event_extra
            )
        else:
            await publish_event(
                "nomination.hrbp-approved", nomination_id, extra=event_extra
            )
            # The manager receives the ordinary approval request only after HRBP
            # has cleared the integrity concern.
            await publish_event("nomination.created", nomination_id)
    except Exception as e:
        logger.warning(
            "Failed to publish HRBP outcome events for %d: %s",
            nomination_id,
            e,
            extra={"nomination_id": nomination_id},
        )

    return {
        "status": result["status"],
        "nomination_id": nomination_id,
        "outcome": result["outcome"],
        "training_disposition": result["training_disposition"],
    }


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
