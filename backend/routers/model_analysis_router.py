"""Tenant-scoped model analysis for Data Scientists and administrators.

Model evidence and active policies are read-only. Data Scientists may submit
fine-tuning requests; Graph policy mutation remains on Admin-only routes.
"""

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

import utils.sqlhelper2 as sqlhelper
from utils import model_artifacts
from auth import is_admin, require_analytics_access


router = APIRouter(prefix="/api/model-analysis", tags=["model-analysis"])

NominationStatus = Literal[
    "Submitted", "Pending", "PendingHRBPReview", "Approved", "Paid", "Rejected"
]
RiskLevel = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE", "UNKNOWN"]
ModelComponent = Literal["rf", "gnn"]
_GRAPH_PATTERNS = {
    "Ring", "SuperNominator", "Desert", "CopyPaste",
    "TransactionalLanguage", "HiddenCandidate",
}


class GraphFineTuningRequest(BaseModel):
    pattern_type: Optional[str] = None
    request_text: str
    suggested_parameters: Optional[dict] = None
    supporting_nomination_ids: list[int] = Field(default_factory=list)


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


@router.get("/setup/graph-policy")
async def get_graph_scoring_policy(
    user_context: dict = Depends(require_analytics_access),
):
    """Inspect the effective tenant's Graph policy and fine-tuning requests."""
    tenant_id = user_context["effective_user"]["TenantId"]
    result = sqlhelper.get_graph_scoring_policy_bundle(tenant_id)
    can_edit = bool(
        is_admin(user_context["actual_user"])
        and not user_context.get("is_impersonating")
    )
    result["can_edit"] = can_edit
    result["can_request"] = not user_context.get("is_impersonating")
    if not can_edit:
        result["draft_policy"] = None
        result["history"] = [
            item for item in result.get("history", [])
            if item.get("status") != "DRAFT"
        ]
    return result


@router.post("/setup/graph-policy/requests")
async def request_graph_scoring_change(
    payload: GraphFineTuningRequest,
    user_context: dict = Depends(require_analytics_access),
):
    if user_context.get("is_impersonating"):
        raise HTTPException(
            status_code=403,
            detail="Fine-tuning requests cannot be submitted while impersonating",
        )
    text_value = payload.request_text.strip()
    if not text_value:
        raise HTTPException(status_code=422, detail="Please describe the requested change")
    if len(text_value) > 2000:
        raise HTTPException(status_code=422, detail="The request must be 2,000 characters or fewer")
    nomination_ids = list(dict.fromkeys(payload.supporting_nomination_ids))
    if payload.pattern_type and payload.pattern_type not in _GRAPH_PATTERNS:
        raise HTTPException(status_code=422, detail="Unknown Graph detector")
    if any(value <= 0 for value in nomination_ids):
        raise HTTPException(status_code=422, detail="Nomination numbers must be positive")
    effective = user_context["effective_user"]
    try:
        request_id = sqlhelper.create_graph_scoring_change_request(
            tenant_id=effective["TenantId"],
            actor=effective.get("userPrincipalName", "unknown"),
            request_text=text_value,
            pattern_type=payload.pattern_type,
            suggested_parameters=payload.suggested_parameters,
            supporting_nomination_ids=nomination_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"request_id": request_id, "status": "REQUESTED"}


@router.get("/setup/models/{component}")
async def get_model_manifest(
    component: ModelComponent,
    user_context: dict = Depends(require_analytics_access),
):
    """Return safe model metadata, never the executable model artifact."""
    tenant_id = user_context["effective_user"]["TenantId"]
    try:
        manifest = model_artifacts.get_manifest(tenant_id, component)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"The {component.upper()} model representation is invalid",
        ) from exc
    if manifest is None:
        return {
            "available": False,
            "component": component,
            "message": "Representation will be available after the next training run.",
        }
    return {"available": True, "component": component, "manifest": manifest}


@router.get("/setup/models/rf/visualization")
async def get_rf_model_visualization(
    user_context: dict = Depends(require_analytics_access),
):
    """Proxy the tenant RF chart without exposing Blob Storage credentials."""
    tenant_id = user_context["effective_user"]["TenantId"]
    image = model_artifacts.get_rf_visualization(tenant_id)
    if image is None:
        raise HTTPException(status_code=404, detail="RF visualization is not available")
    return Response(
        content=image,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


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
