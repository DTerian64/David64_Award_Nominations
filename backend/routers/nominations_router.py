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
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi import Form as FastAPIForm
from fastapi.responses import HTMLResponse
from typing import List

import utils.sqlhelper2 as sqlhelper
from auth import get_current_user_with_impersonation, log_action_if_impersonating
from routers.schemas import (
    CertificateResponse, Nomination, NominationApproval, NominationCreate,
    StatusResponse, User,
)
from utils.certificate import get_or_create_certificate
from utils.service_bus_publisher import publish_event
from utils.token_utils import verify_action_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["nominations"])


# ── HTML confirmation page for email-action links ─────────────────────────────

def get_action_confirmation_page(
    action: str,
    success: bool,
    message: str,
    dashboard_url: str | None = None,
) -> str:
    """
    HTML page shown in the browser after a manager clicks approve/reject in email.

    Args:
        action:        "approved" or "rejected"
        success:       whether the action succeeded
        message:       detail message to display
        dashboard_url: per-tenant Site_URL from dbo.Tenants; if None the
                       'Go to Dashboard' button is omitted entirely
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
            {f'<a href="{dashboard_url}" class="button">Go to Dashboard</a>' if dashboard_url else ''}
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


def _warm_certificate_if_attaching(nomination_id: int) -> None:
    """
    When a tenant has opted into attaching the certificate to the beneficiary
    email, pre-generate (warm the cache for) the certificate at approval time so
    the auxiliary worker finds the PDF already in blob storage when it sends the
    email. No-op (and never raises) for tenants with the feature off — those
    certificates are generated lazily on the manager's first link click instead.
    """
    try:
        data = sqlhelper.get_nomination_for_certificate(nomination_id)
        if not data:
            return
        cfg = sqlhelper.get_tenant_certificate_config(data["tenant_id"])
        if not (cfg.enabled and cfg.attach_to_beneficiary):
            return
        result = get_or_create_certificate(nomination_id, template_blob=cfg.template_blob)
        if result.get("status") != "success":
            logger.warning(
                "Certificate warm-up for nomination %d returned: %s",
                nomination_id, result.get("message"),
            )
    except Exception as e:
        # Never let certificate generation block or fail the approval.
        logger.warning("Certificate warm-up failed for nomination %d: %s", nomination_id, e)


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

    # ── Tenant config (currency + award limits) ───────────────────────────────
    _raw_cfg    = sqlhelper.get_tenant_config(tenant_id)
    _currency   = "USD"
    _min_award  = 50
    _max_award  = 5000
    if _raw_cfg:
        try:
            _cfg       = _json.loads(_raw_cfg)
            _currency  = _cfg.get("currency",   "USD")
            _min_award = int(_cfg.get("min_award", 50))
            _max_award = int(_cfg.get("max_award", 5000))
        except Exception:
            pass

    # ── Award amount validation (server-authoritative) ────────────────────────
    _amount = int(nomination.Amount)
    if not (_min_award <= _amount <= _max_award):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Award amount must be between {_min_award} and {_max_award} "
                f"{_currency} for this tenant."
            ),
        )

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

        # ── Category-specific amount limits (narrower than tenant limits) ─────
        _cat = next((c for c in _categories if c[0] == nomination.CategoryId), None)
        if _cat and (_cat[2] is not None or _cat[3] is not None):
            _cat_min = _cat[2] if _cat[2] is not None else _min_award
            _cat_max = _cat[3] if _cat[3] is not None else _max_award
            if not (_cat_min <= _amount <= _cat_max):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"For this award category, the amount must be between "
                        f"{_cat_min} and {_cat_max} {_currency}."
                    ),
                )

    # ── Description quality validation (API-layer, synchronous) ──────────────
    # Structural checks only — no embedding model involved.
    # Semantic checks (category alignment, duplicate detection) run async in
    # the integrity-check pipeline after the nomination is saved.
    _desc_cfg = sqlhelper.get_tenant_desc_check_config(tenant_id)
    _desc     = nomination.NominationDescription.strip()

    # Length gate — word count for Western languages, char count for CJK
    if _desc_cfg.use_char_count:
        if len(_desc) < _desc_cfg.min_char_count:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Nomination description is too short — please provide at least "
                    f"{_desc_cfg.min_char_count} characters that describe what "
                    f"this person did and why it was impactful."
                ),
            )
    else:
        _word_count = len(re.findall(r"\S+", _desc))
        if _word_count < _desc_cfg.min_word_count:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Nomination description is too short — please use at least "
                    f"{_desc_cfg.min_word_count} words to describe what this person "
                    f"did and why it was impactful."
                ),
            )

    # Boilerplate phrase check — exact match against lowercased description
    if _desc_cfg.boilerplate_phrases:
        _desc_lower = _desc.lower()
        for _phrase in _desc_cfg.boilerplate_phrases:
            if _phrase in _desc_lower:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Nomination description appears too generic. "
                        f"Please describe a specific action or achievement — "
                        f"what did this person do, and what was the impact?"
                    ),
                )

    # ── Save nomination — status starts as Submitted ──────────────────────────
    # Fraud detection runs asynchronously in the auxiliary service after the
    # nomination.submitted event is consumed. The auxiliary service moves the
    # status to Pending (clean) or PendingHRBPReview (flagged) and publishes
    # the appropriate downstream event (nomination.created / nomination.fraud-flagged).
    nomination_id = sqlhelper.create_nomination(
        nominator_id=effective_user["UserId"],
        beneficiary_id=nomination.BeneficiaryId,
        approver_id=manager_id,
        amount=nomination.Amount,
        currency=_currency,
        description=nomination.NominationDescription,
        category_id=nomination.CategoryId,
        initial_status='Submitted',
    )

    logger.info(
        "Nomination saved — publishing nomination.submitted for async fraud check",
        extra={
            "nomination_id": nomination_id,
            "user_id":       effective_user["UserId"],
        }
    )

    await log_action_if_impersonating(
        user_context,
        "created_nomination",
        f"NominationId: {nomination_id}, Beneficiary: {beneficiary_name}, Amount: {nomination.Amount} {_currency}"
    )

    try:
        await publish_event("nomination.submitted", int(nomination_id))
    except Exception as e:
        logger.warning(
            "⚠️ Failed to publish nomination.submitted for nomination %d: %s",
            nomination_id, e
        )

    return StatusResponse(
        Status="Submitted",
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


@router.get("/api/nominations/my-approvals", response_model=List[Nomination])
async def get_my_approvals(user_context: dict = Depends(get_current_user_with_impersonation)):
    """Nominations the current user (as approver) has already decided — the
    Approved / Rejected view of the My Approvals tab."""
    effective_user = user_context["effective_user"]
    tenant_id      = effective_user["TenantId"]

    rows = sqlhelper.get_decided_nominations_for_approver(effective_user["UserId"], tenant_id)

    nominations = [
        Nomination(
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
            RejectionReason=row[12],
            RejectionActor=row[13],
        )
        for row in rows
    ]

    await log_action_if_impersonating(user_context, "viewed_my_approvals")
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

        # Warm the certificate cache before the event fires, so the worker can
        # attach the PDF if this tenant has opted in (no-op otherwise).
        _warm_certificate_if_attaching(approval.NominationId)

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
        sqlhelper.reject_nomination(
            approval.NominationId,
            reason=approval.reason,
            actor="Manager",
        )

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
            RejectionReason=row[12],
            RejectionActor=row[13],
        ))

    await log_action_if_impersonating(user_context, "viewed_nomination_history")
    return nominations


@router.get("/api/nominations/{nomination_id}/certificate", response_model=CertificateResponse)
async def get_nomination_certificate(
    nomination_id: int,
    user_context: dict = Depends(get_current_user_with_impersonation),
):
    """
    Return a SAS link to the award certificate for an approved nomination.

    Authorization: only the nomination's approver may request its certificate.
    Gated on the tenant's certificate_config.enabled flag (feature is off by
    default). The PDF is generated lazily on first request and cached, so
    repeated calls reuse the same blob.
    """
    effective_user = user_context["effective_user"]
    tenant_id      = effective_user["TenantId"]

    # Tenant-scoped approver check (prevents cross-tenant probing).
    approver_id = sqlhelper.get_nomination_approver(nomination_id, tenant_id)
    if approver_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nomination not found")
    if approver_id != effective_user["UserId"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this certificate",
        )

    cfg = sqlhelper.get_tenant_certificate_config(tenant_id)
    if not cfg.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Award certificates are not enabled for this organisation",
        )

    # Certificates only exist for approved nominations.
    nomination_status = sqlhelper.get_nomination_status(nomination_id)
    if nomination_status not in ("Approved", "Paid"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A certificate is only available for approved nominations",
        )

    result = get_or_create_certificate(nomination_id, template_blob=cfg.template_blob)
    if result.get("status") != "success":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not generate certificate: {result.get('message', 'unknown error')}",
        )

    await log_action_if_impersonating(
        user_context, "generated_certificate", f"NominationId: {nomination_id}"
    )
    return CertificateResponse(DownloadUrl=result["download_url"], Cached=result["cached"])


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

    # Resolve the tenant's dashboard URL from the approver's tenant record.
    # None if Site_URL is not configured — the button will be omitted.
    dashboard_url = sqlhelper.get_site_url_by_user_id(expected_approver_id)

    try:
        actual_approver_id = sqlhelper.get_nomination_approver(nomination_id)

        if actual_approver_id is None:
            return get_action_confirmation_page(dashboard_url=dashboard_url,
                action="",
                success=False,
                message="Nomination not found. It may have already been processed or deleted."
            )

        if actual_approver_id != expected_approver_id:
            return get_action_confirmation_page(dashboard_url=dashboard_url,
                action="",
                success=False,
                message="You are not authorized to approve or reject this nomination."
            )

        nomination_status = sqlhelper.get_nomination_status(nomination_id)
    except Exception as e:
        logger.error("❌ Error looking up nomination %d for email action: %s", nomination_id, e)
        return get_action_confirmation_page(dashboard_url=dashboard_url,
            action="",
            success=False,
            message="An error occurred while looking up the nomination. Please try again or log in to the Award Nomination System."
        )

    if nomination_status in ["Approved", "Rejected"]:
        return get_action_confirmation_page(dashboard_url=dashboard_url,
            action=nomination_status.lower(),
            success=True,
            message=f"This nomination has already been {nomination_status.lower()}."
        )

    try:
        if action == "approve":
            sqlhelper.approve_nomination(nomination_id)

            # Warm the certificate cache before the event fires (no-op unless
            # this tenant attaches certificates to the beneficiary email).
            _warm_certificate_if_attaching(nomination_id)

            try:
                await publish_event("nomination.approved", nomination_id)
            except Exception as e:
                logger.warning(
                    "⚠️ Failed to publish nomination.approved event for nomination %d: %s",
                    nomination_id, e
                )

            return get_action_confirmation_page(dashboard_url=dashboard_url,
                action="approved",
                success=True,
                message="The nomination has been approved successfully. The nominator has been notified via email."
            )

        else:  # action == "reject"
            # Two-step flow: show a reason form instead of acting immediately.
            # The manager submits the form → POST /api/nominations/email-action
            # carries the same token + the typed reason and commits the rejection.
            return _get_rejection_reason_page(token, dashboard_url)

    except Exception as e:
        logger.error(f"❌ Error processing email action: {e}")
        return get_action_confirmation_page(dashboard_url=dashboard_url,
            action="",
            success=False,
            message=f"An error occurred while processing your request: {str(e)}"
        )


def _get_rejection_reason_page(token: str, dashboard_url: str | None = None) -> str:
    """
    HTML form page shown when a manager clicks the email 'Reject' link.
    Submits to POST /api/nominations/email-action with the same token + reason.
    """
    dashboard_btn = (
        f'<a href="{dashboard_url}" class="button secondary">Go to Dashboard</a>'
        if dashboard_url else ""
    )
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Reject Nomination</title>
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
                max-width: 500px;
                width: 100%;
            }}
            .icon {{ font-size: 48px; margin-bottom: 16px; text-align: center; }}
            h1 {{ color: #e74c3c; margin-bottom: 8px; text-align: center; }}
            p {{ color: #666; margin-bottom: 20px; text-align: center; }}
            label {{ display: block; font-weight: bold; color: #333; margin-bottom: 6px; }}
            textarea {{
                width: 100%;
                min-height: 120px;
                padding: 10px;
                border: 1px solid #ccc;
                border-radius: 6px;
                font-size: 15px;
                font-family: Arial, sans-serif;
                resize: vertical;
                box-sizing: border-box;
            }}
            textarea:focus {{ outline: none; border-color: #e74c3c; box-shadow: 0 0 0 2px rgba(231,76,60,0.2); }}
            .buttons {{ display: flex; gap: 12px; margin-top: 20px; }}
            .button {{
                flex: 1;
                padding: 12px;
                border: none;
                border-radius: 6px;
                font-size: 15px;
                font-weight: bold;
                cursor: pointer;
                text-align: center;
                text-decoration: none;
                display: inline-block;
            }}
            .button.primary {{ background: #e74c3c; color: white; }}
            .button.primary:hover {{ background: #c0392b; }}
            .button.secondary {{ background: #f0f0f0; color: #333; }}
            .button.secondary:hover {{ background: #e0e0e0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="icon">❌</div>
            <h1>Reject Nomination</h1>
            <p>Please provide a reason. The nominator will be notified.</p>
            <form method="POST" action="/api/nominations/email-action">
                <input type="hidden" name="token" value="{token}" />
                <label for="reason">Reason for rejection</label>
                <textarea id="reason" name="reason" placeholder="e.g. This nomination does not meet the award criteria because…" required></textarea>
                <div class="buttons">
                    <button type="submit" class="button primary">Confirm Rejection</button>
                    {dashboard_btn}
                </div>
            </form>
        </div>
    </body>
    </html>
    """


@router.post("/api/nominations/email-action", response_class=HTMLResponse)
async def handle_email_action_post(
    token:  str = FastAPIForm(...),
    reason: str = FastAPIForm(""),
):
    """
    Step 2 of the email reject flow.

    Receives the signed token (same JWT from the email link) and the typed
    rejection reason from the HTML form, verifies the token, and commits the
    rejection.  Approve actions never reach this endpoint.
    """
    payload = verify_action_token(token)
    if not payload:
        return get_action_confirmation_page(
            action="",
            success=False,
            message="This link has expired or is invalid. Please log in to the Award Nomination System to reject this nomination.",
        )

    nomination_id        = payload["nomination_id"]
    action               = payload["action"]
    expected_approver_id = payload["approver_id"]
    dashboard_url        = sqlhelper.get_site_url_by_user_id(expected_approver_id)

    if action != "reject":
        # Sanity guard — this endpoint should only receive reject tokens.
        return get_action_confirmation_page(dashboard_url=dashboard_url,
            action="", success=False,
            message="Invalid action for this endpoint.",
        )

    try:
        actual_approver_id = sqlhelper.get_nomination_approver(nomination_id)
        if actual_approver_id is None:
            return get_action_confirmation_page(dashboard_url=dashboard_url,
                action="", success=False,
                message="Nomination not found. It may have already been processed or deleted.",
            )
        if actual_approver_id != expected_approver_id:
            return get_action_confirmation_page(dashboard_url=dashboard_url,
                action="", success=False,
                message="You are not authorized to reject this nomination.",
            )

        nomination_status = sqlhelper.get_nomination_status(nomination_id)
    except Exception as e:
        logger.error("❌ Error looking up nomination %d for email reject POST: %s", nomination_id, e)
        return get_action_confirmation_page(dashboard_url=dashboard_url,
            action="", success=False,
            message="An error occurred while looking up the nomination. Please try again.",
        )

    if nomination_status in ["Approved", "Rejected"]:
        return get_action_confirmation_page(dashboard_url=dashboard_url,
            action=nomination_status.lower(),
            success=True,
            message=f"This nomination has already been {nomination_status.lower()}.",
        )

    try:
        sqlhelper.reject_nomination(nomination_id, reason=reason, actor="Manager")

        try:
            await publish_event("nomination.approved", nomination_id)
        except Exception as e:
            logger.warning(
                "⚠️ Failed to publish nomination.approved event for nomination %d: %s",
                nomination_id, e,
            )

        logger.info(
            "Manager rejected nomination %d via email action (reason=%r)",
            nomination_id, reason,
        )
        return get_action_confirmation_page(dashboard_url=dashboard_url,
            action="rejected",
            success=True,
            message="The nomination has been rejected. The nominator has been notified via email.",
        )

    except Exception as e:
        logger.error("❌ Error processing email reject POST: %s", e)
        return get_action_confirmation_page(dashboard_url=dashboard_url,
            action="", success=False,
            message=f"An error occurred while processing your request: {str(e)}",
        )
