"""
routers/nominations_router.py
==============================
Nomination lifecycle endpoints.

Routes
------
POST /api/nominations                              — submit a nomination
GET  /api/nominations/pending                      — manager's approval queue
POST /api/nominations/approve                      — approve or reject
GET  /api/nominations/history                      — nominator's own history
GET  /api/nominations/email-action                 — token-based email approve/reject
"""

import logging
import json as _json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from typing import List

import utils.sqlhelper2 as sqlhelper
import fraud_ml
from auth import get_current_user_with_impersonation, log_action_if_impersonating
from routers.schemas import Nomination, NominationApproval, NominationCreate, StatusResponse, User
from utils.service_bus_publisher import publish_event
from utils.token_utils import verify_action_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["nominations"])


# ── HTML confirmation page for email-action links ─────────────────────────────

def get_action_confirmation_page(action: str, success: bool, message: str) -> str:
    """
    HTML page shown in the browser after a manager clicks approve/reject in email.

    Args:
        action:  "approved" or "rejected"
        success: whether the action succeeded
        message: detail message to display
    """
    if success:
        color = "#27ae60" if action == "approved" else "#e74c3c"
        icon  = "✅" if action == "approved" else "❌"
        title = f"Nomination {action.title()}"
    else:
        color = "#e74c3c"
        icon  = "⚠️"
        title = "Action Failed"

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            }}
            .container {{
                background: white;
                padding: 40px;
                border-radius: 10px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                text-align: center;
                max-width: 500px;
            }}
            .icon {{ font-size: 72px; margin-bottom: 20px; }}
            h1 {{ color: {color}; margin-bottom: 20px; }}
            p {{ font-size: 18px; color: #666; line-height: 1.6; }}
            .button {{
                display: inline-block;
                background-color: #667eea;
                color: white;
                padding: 12px 30px;
                text-decoration: none;
                border-radius: 5px;
                margin-top: 20px;
                font-weight: bold;
            }}
            .button:hover {{ background-color: #5568d3; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="icon">{icon}</div>
            <h1>{title}</h1>
            <p>{message}</p>
            <a href="https://awards.terian-services.com" class="button">Go to Dashboard</a>
        </div>
    </body>
    </html>
    """


# ── Helpers ───────────────────────────────────────────────────────────────────

async def generate_payroll_extract(nomination_id: int):
    """Generate payroll extract file for approved nomination (future phase)."""
    row = sqlhelper.get_nomination_for_payroll(nomination_id)

    if row:
        extract_filename = f"payroll_extract_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        with open(extract_filename, 'w') as f:
            f.write("EmployeeId,FirstName,LastName,AwardAmount,Date\n")
            f.write(f"{row[0]},{row[3]},{row[4]},{row[1]},{row[2]}\n")

        sqlhelper.mark_nomination_as_paid(nomination_id)
        logger.info(f"Payroll extract generated: {extract_filename}")

        # In production, upload to Azure Blob Storage or SFTP to payroll system


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/api/nominations", status_code=status.HTTP_201_CREATED, response_model=StatusResponse)
async def create_nomination(
    nomination: NominationCreate,
    user_context: dict = Depends(get_current_user_with_impersonation)
):
    """Create a new nomination"""
    effective_user = user_context["effective_user"]
    tenant_id      = effective_user["TenantId"]

    logger.info(
        "Nomination submission started",
        extra={
            "user_id": effective_user["UserId"],
            "beneficiary_id": nomination.BeneficiaryId,
            "amount": float(nomination.Amount)
        }
    )

    # Get beneficiary's manager — scoped to same tenant
    beneficiary = sqlhelper.get_user_manager_info(nomination.BeneficiaryId, tenant_id)

    if not beneficiary:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Beneficiary not found"
        )

    manager_id = beneficiary[0]
    beneficiary_name = f"{beneficiary[1]} {beneficiary[2]}"

    if not manager_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Beneficiary has no manager assigned"
        )

    # Get manager info
    manager = sqlhelper.get_user_name_by_id(manager_id)
    if not manager:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Manager data inconsistency: Manager ID {manager_id} not found in system"
        )

    manager_name = f"{manager[0]} {manager[1]}"

    # Get fraud assessment
    logger.info("Getting fraud assessment for nomination", extra={
        "nomination": nomination,
        "manager_id": manager_id
    })
    try:
        fraud_result = fraud_ml.get_fraud_assessment({
            'TenantId':      tenant_id,
            'NominatorId':   effective_user["UserId"],
            'BeneficiaryId': nomination.BeneficiaryId,
            'ApproverId':    manager_id,
            'Amount':        nomination.Amount,
            'NominationDate': datetime.now(),
            'CategoryId':    nomination.CategoryId,
        })
    except Exception as fraud_exc:
        logger.error("Fraud assessment raised an unhandled exception — defaulting to MANUAL_REVIEW", extra={"error": str(fraud_exc)})
        fraud_result = {
            'fraud_probability': 0.0,
            'fraud_score': 0,
            'risk_level': 'UNKNOWN',
            'warning_flags': ['Fraud check unavailable — manual review required'],
            'recommendation': 'MANUAL_REVIEW'
        }

    if fraud_result['risk_level'] in ('CRITICAL', 'HIGH'):
        logger.warning("Fraud assessment result", extra={
            "risk_level": fraud_result['risk_level'],
            "fraud_score": fraud_result['fraud_score'],
            "warning_flags": fraud_result['warning_flags']
        })
    else:
        logger.info("Fraud assessment result", extra={
            "risk_level": fraud_result['risk_level'],
            "fraud_score": fraud_result['fraud_score'],
            "warning_flags": fraud_result['warning_flags']
        })

    # ── HRBP routing: MEDIUM / HIGH / CRITICAL → hold for HRBP review ────────
    _flagged_for_hrbp = fraud_result['risk_level'] in ('MEDIUM', 'HIGH', 'CRITICAL')

    # Resolve the tenant's currency from the DB config (server-authoritative)
    _raw_cfg = sqlhelper.get_tenant_config(tenant_id)
    _currency = "USD"
    if _raw_cfg:
        try:
            _currency = _json.loads(_raw_cfg).get("currency", "USD")
        except Exception:
            pass

    # ── Category validation ───────────────────────────────────────────────────
    _categories = sqlhelper.get_nomination_categories(tenant_id)
    if _categories:
        if nomination.CategoryId is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CategoryId is required for this tenant",
            )
        _valid_ids = {row[0] for row in _categories}
        if nomination.CategoryId not in _valid_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"CategoryId {nomination.CategoryId} is not valid for this tenant",
            )

    nomination_id = sqlhelper.create_nomination(
        nominator_id=effective_user["UserId"],
        beneficiary_id=nomination.BeneficiaryId,
        approver_id=manager_id,
        amount=nomination.Amount,
        currency=_currency,
        description=nomination.NominationDescription,
        category_id=nomination.CategoryId,
    )

    if _flagged_for_hrbp:
        sqlhelper.set_nomination_status(nomination_id, "PendingHRBPReview")

    logger.info(
        "Nomination created successfully",
        extra={
            "nomination_id": nomination_id,
            "user_id":       effective_user["UserId"],
            "hrbp_flagged":  _flagged_for_hrbp,
        }
    )

    # Persist the P2P fraud score
    try:
        sqlhelper.save_p2p_fraud_score(
            nomination_id=nomination_id,
            fraud_score=fraud_result['fraud_score'],
            risk_level=fraud_result['risk_level'],
            warning_flags=", ".join(fraud_result.get('warning_flags', [])),
        )
    except Exception as save_exc:
        logger.error(
            "Failed to save P2P fraud score for nomination %d: %s",
            nomination_id, save_exc
        )

    # Persist the richer HRBP snapshot for the HRBP review queue.
    if _flagged_for_hrbp:
        try:
            top_features    = fraud_result.get('top_features')
            feature_summary = fraud_result.get('feature_summary')
            sqlhelper.save_hrbp_fraud_flags(
                nomination_id=nomination_id,
                fraud_score=fraud_result['fraud_score'],
                fraud_probability=fraud_result.get('fraud_probability', 0.0),
                risk_level=fraud_result['risk_level'],
                warning_flags=", ".join(fraud_result.get('warning_flags', [])),
                top_features_json=_json.dumps(top_features) if top_features else None,
                feature_summary_json=_json.dumps(feature_summary) if feature_summary else None,
            )
        except Exception as flag_exc:
            logger.error(
                "Failed to save HRBP fraud flags for nomination %d: %s",
                nomination_id, flag_exc
            )

    await log_action_if_impersonating(
        user_context,
        "created_nomination",
        f"NominationId: {nomination_id}, Beneficiary: {beneficiary_name}, Amount: {nomination.Amount} {_currency}"
    )

    try:
        if _flagged_for_hrbp:
            await publish_event(
                "nomination.fraud-flagged",
                int(nomination_id),
                extra={"risk_level": fraud_result['risk_level']},
            )
        else:
            await publish_event("nomination.created", int(nomination_id))
    except Exception as e:
        logger.warning(
            "⚠️ Failed to publish event for nomination %d: %s",
            nomination_id, e
        )

    if _flagged_for_hrbp:
        return StatusResponse(
            Status="PendingHRBPReview",
            Message="Your nomination is being reviewed by HR before proceeding. You will be notified of the outcome."
        )

    return StatusResponse(
        Status="Pending",
        Message="Nomination submitted successfully"
    )


@router.get("/api/nominations/pending", response_model=List[Nomination])
async def get_pending_nominations(user_context: dict = Depends(get_current_user_with_impersonation)):
    """Get nominations pending approval for current user (as manager)"""
    effective_user = user_context["effective_user"]
    tenant_id      = effective_user["TenantId"]

    rows = sqlhelper.get_pending_nominations_for_approver(effective_user["UserId"], tenant_id)

    nominations = []
    for row in rows:
        nominations.append(Nomination(
            NominationId=row[0],
            NominatorId=row[1],
            BeneficiaryId=row[2],
            ApproverId=row[3],
            Amount=row[4],
            Currency=row[5],
            NominationDescription=row[6],
            NominationDate=row[7],
            ApprovedDate=row[8],
            PayedDate=row[9],
            Status=row[10],
            CategoryDescription=row[11],
        ))

    await log_action_if_impersonating(user_context, "viewed_pending_approvals")
    return nominations


@router.post("/api/nominations/approve", response_model=StatusResponse)
async def approve_nomination(
    approval: NominationApproval,
    user_context: dict = Depends(get_current_user_with_impersonation)
):
    """Approve or reject a nomination"""
    effective_user = user_context["effective_user"]
    tenant_id      = effective_user["TenantId"]

    approver_id = sqlhelper.get_nomination_approver(approval.NominationId, tenant_id)

    if approver_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nomination not found"
        )

    if approver_id != effective_user["UserId"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to approve this nomination"
        )

    if approval.Approved:
        sqlhelper.approve_nomination(approval.NominationId)

        try:
            await publish_event("nomination.approved", approval.NominationId)
        except Exception as e:
            logger.warning(
                "⚠️ Failed to publish nomination.approved event for nomination %d: %s",
                approval.NominationId, e
            )

        await log_action_if_impersonating(
            user_context,
            "approved_nomination",
            f"NominationId: {approval.NominationId}"
        )

        return StatusResponse(
            Status="Approved",
            Message="Nomination approved successfully"
        )
    else:
        sqlhelper.reject_nomination(approval.NominationId)

        try:
            await publish_event("nomination.approved", approval.NominationId)
        except Exception as e:
            logger.warning(
                "⚠️ Failed to publish nomination.approved event for nomination %d: %s",
                approval.NominationId, e
            )

        await log_action_if_impersonating(
            user_context,
            "rejected_nomination",
            f"NominationId: {approval.NominationId}"
        )

        return StatusResponse(
            Status="Rejected",
            Message="Nomination rejected"
        )


@router.get("/api/nominations/history", response_model=List[Nomination])
async def get_nomination_history(user_context: dict = Depends(get_current_user_with_impersonation)):
    """Get nomination history for current user"""
    effective_user = user_context["effective_user"]
    tenant_id      = effective_user["TenantId"]

    rows = sqlhelper.get_nomination_history(effective_user["UserId"], tenant_id)

    nominations = []
    for row in rows:
        nominations.append(Nomination(
            NominationId=row[0],
            NominatorId=row[1],
            BeneficiaryId=row[2],
            ApproverId=row[3],
            Amount=row[4],
            Currency=row[5],
            NominationDescription=row[6],
            NominationDate=row[7],
            ApprovedDate=row[8],
            PayedDate=row[9],
            Status=row[10],
            CategoryDescription=row[11],
        ))

    await log_action_if_impersonating(user_context, "viewed_nomination_history")
    return nominations


@router.get("/api/nominations/email-action", response_class=HTMLResponse)
async def handle_email_action(token: str = Query(..., description="Action token from email")):
    """
    Handle approve/reject action from email button click.

    Verifies the token, checks authorization, performs the action,
    and returns an HTML confirmation page.

    Security: token is a signed JWT (72-hour expiry, contains approver_id).
    """
    payload = verify_action_token(token)

    if not payload:
        return get_action_confirmation_page(
            action="",
            success=False,
            message="This link has expired or is invalid. Please log in to the Award Nomination System to approve or reject this nomination."
        )

    nomination_id        = payload["nomination_id"]
    action               = payload["action"]
    expected_approver_id = payload["approver_id"]

    try:
        actual_approver_id = sqlhelper.get_nomination_approver(nomination_id)

        if actual_approver_id is None:
            return get_action_confirmation_page(
                action="",
                success=False,
                message="Nomination not found. It may have already been processed or deleted."
            )

        if actual_approver_id != expected_approver_id:
            return get_action_confirmation_page(
                action="",
                success=False,
                message="You are not authorized to approve or reject this nomination."
            )

        nomination_status = sqlhelper.get_nomination_status(nomination_id)
    except Exception as e:
        logger.error("❌ Error looking up nomination %d for email action: %s", nomination_id, e)
        return get_action_confirmation_page(
            action="",
            success=False,
            message="An error occurred while looking up the nomination. Please try again or log in to the Award Nomination System."
        )

    if nomination_status in ["Approved", "Rejected"]:
        return get_action_confirmation_page(
            action=nomination_status.lower(),
            success=True,
            message=f"This nomination has already been {nomination_status.lower()}."
        )

    try:
        if action == "approve":
            sqlhelper.approve_nomination(nomination_id)

            try:
                await publish_event("nomination.approved", nomination_id)
            except Exception as e:
                logger.warning(
                    "⚠️ Failed to publish nomination.approved event for nomination %d: %s",
                    nomination_id, e
                )

            return get_action_confirmation_page(
                action="approved",
                success=True,
                message="The nomination has been approved successfully. The nominator has been notified via email."
            )

        else:  # action == "reject"
            sqlhelper.reject_nomination(nomination_id)

            try:
                await publish_event("nomination.approved", nomination_id)
            except Exception as e:
                logger.warning(
                    "⚠️ Failed to publish nomination.approved event for nomination %d: %s",
                    nomination_id, e
                )

            return get_action_confirmation_page(
                action="rejected",
                success=True,
                message="The nomination has been rejected. The nominator has been notified via email."
            )

    except Exception as e:
        logger.error(f"❌ Error processing email action: {e}")
        return get_action_confirmation_page(
            action="",
            success=False,
            message=f"An error occurred while processing your request: {str(e)}"
        )
