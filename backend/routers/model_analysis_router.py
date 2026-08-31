"""Read-only nomination model analysis for Data Scientists and administrators."""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import utils.sqlhelper2 as sqlhelper
from auth import require_analytics_access


router = APIRouter(prefix="/api/model-analysis", tags=["model-analysis"])

NominationStatus = Literal[
    "Submitted", "Pending", "PendingHRBPReview", "Approved", "Paid", "Rejected"
]
RiskLevel = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN"]


@router.get("/nominations")
async def search_nominations(
    q: str = Query(default="", max_length=200),
    nomination_status: Optional[NominationStatus] = Query(default=None, alias="status"),
    risk: Optional[RiskLevel] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    user_context: dict = Depends(require_analytics_access),
):
    """Search the effective user's tenant; never returns cross-tenant rows."""
    tenant_id = user_context["effective_user"]["TenantId"]
    return sqlhelper.search_model_analysis_nominations(
        tenant_id=tenant_id,
        query=q,
        status_filter=nomination_status,
        risk_filter=risk,
        page=page,
        page_size=page_size,
    )


@router.get("/nominations/{nomination_id}")
async def get_nomination_analysis(
    nomination_id: int,
    user_context: dict = Depends(require_analytics_access),
):
    tenant_id = user_context["effective_user"]["TenantId"]
    result = sqlhelper.get_model_analysis_nomination(nomination_id, tenant_id)
    if not result:
        raise HTTPException(status_code=404, detail="Nomination not found")
    return result


@router.get("/nominations/{nomination_id}/pair-history")
async def get_nomination_pair_history(
    nomination_id: int,
    user_context: dict = Depends(require_analytics_access),
):
    tenant_id = user_context["effective_user"]["TenantId"]
    details = sqlhelper.get_nomination_details_for_hrbp(nomination_id)
    if not details or details["tenant_id"] != tenant_id:
        raise HTTPException(status_code=404, detail="Nomination not found")
    history = sqlhelper.get_pair_nomination_history(
        nominator_id=details["nominator_id"],
        beneficiary_id=details["beneficiary_id"],
        tenant_id=tenant_id,
        exclude_nomination_id=nomination_id,
    )
    return {
        "nominator_name": details["nominator_name"],
        "beneficiary_name": details["beneficiary_name"],
        "pair_count": len(history),
        "history": history,
    }
