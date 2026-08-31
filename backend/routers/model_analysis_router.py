"""Read-only nomination model analysis for Data Scientists and administrators."""

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import utils.sqlhelper2 as sqlhelper
from auth import require_analytics_access


router = APIRouter(prefix="/api/model-analysis", tags=["model-analysis"])

NominationStatus = Literal[
    "Submitted", "Pending", "PendingHRBPReview", "Approved", "Paid", "Rejected"
]
RiskLevel = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN"]


@router.get("/setup/fraud-integrity")
async def get_fraud_integrity_setup(
    user_context: dict = Depends(require_analytics_access),
):
    """Return tenant fraud/integrity settings without exposing a write route."""
    tenant_id = user_context["effective_user"]["TenantId"]
    return sqlhelper.get_fraud_settings(tenant_id)


@router.get("/setup/decision-engines")
async def get_decision_engines_setup(
    user_context: dict = Depends(require_analytics_access),
):
    """Return tenant decision-engine operational status as a read-only view."""
    tenant_id = user_context["effective_user"]["TenantId"]
    return {"rows": sqlhelper.get_integrity_component_statuses(tenant_id)}


@router.get("/nominations")
async def search_nominations(
    q: str = Query(default="", max_length=200),
    nomination_status: Optional[NominationStatus] = Query(default=None, alias="status"),
    risk: Optional[RiskLevel] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    user_context: dict = Depends(require_analytics_access),
):
    """Search the effective user's tenant; never returns cross-tenant rows."""
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=422,
            detail="Start date must be on or before end date",
        )
    tenant_id = user_context["effective_user"]["TenantId"]
    return sqlhelper.search_model_analysis_nominations(
        tenant_id=tenant_id,
        query=q,
        status_filter=nomination_status,
        risk_filter=risk,
        start_date=start_date,
        end_date=end_date,
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
