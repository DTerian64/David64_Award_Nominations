# Award Nomination System - FastAPI Application
# Architecture: FastAPI + Azure SQL + Entra ID + Email Notifications

import logging
from logging_config import setup_logging

# Set up logging at the top of the file
setup_logging()
logger = logging.getLogger(__name__)

import socket
from dotenv import load_dotenv
load_dotenv()

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status,HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Any
from datetime import datetime
from pydantic import BaseModel

# Import authentication functions from auth.py
from auth import (
    get_current_user,
    get_current_user_with_impersonation,
    require_role,
    log_action_if_impersonating,
    is_admin
)

import sqlhelper2 as sqlhelper  # Database helper functions for Azure SQL
from models import (
    User, NominationCreate, Nomination, NominationApproval,
    StatusResponse, HealthResponse, AuditLog
)

import fraud_ml

from token_utils import verify_action_token, get_action_url
from email_utils import (
    get_action_confirmation_page, 
    get_nomination_pending_email
)

# ============================================================================
# CONFIGURATION
# ============================================================================

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")

# Email Configuration (Gmail SMTP)
# Import email utilities
from email_utils import send_email, get_nomination_submitted_email, get_nomination_approved_email

# ============================================================================
# FASTAPI APPLICATION
# ============================================================================

from fastapi.openapi.docs import get_swagger_ui_html


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler – runs startup logic, then yields control."""
    # Startup: ensure all ORM-defined tables exist in the database
    sqlhelper.create_all_tables()
    logger.info("Database tables verified on startup.")
    yield
    # Shutdown: nothing to clean up (connection pool is managed per-request)


app = FastAPI(
    lifespan=lifespan,
    title="Award Nomination System",
    description="Employee recognition and monetary award nomination system",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

FastAPIInstrumentor.instrument_app(app)

# ============================================================================
# CORS CONFIGURATION — must be added immediately after app creation, before routes
# ============================================================================
# Format: comma-separated origins, e.g. "https://app.example.com,http://localhost:5173"
# Set CORS_ALLOWED_ORIGINS in Terraform (deployed) or .env (local dev).
_cors_env = os.getenv("CORS_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()]
logger.info("CORS allowed origins: %s", ALLOWED_ORIGINS)  # ← diagnostic: confirm env var is set

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,               # CRITICAL: Must be True for auth
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "X-Requested-With",
        "X-Impersonate-User",
        "traceparent",
        "tracestate",
        "baggage",
        "Request-Id",
        "Correlation-Context",
    ],
    expose_headers=["*"],
    max_age=3600,
)

# ============================================================================
# OBSERVABILITY — Azure Monitor / Application Insights
# configure_azure_monitor() is intentionally NOT called here.
#
# Gunicorn pre-fork model: this module is loaded in the master process, then
# workers are forked. Calling configure_azure_monitor() in the master causes
# OTel background exporter threads to die in child processes and leaves global
# OTel state in a half-initialized form that can interfere with the ASGI
# middleware stack (including Starlette CORSMiddleware).
#
# Instead, configure_azure_monitor() is called in gunicorn.conf.py post_fork(),
# which runs in each worker after forking — OTel is initialized fresh with
# clean thread state per worker.
# ============================================================================


# Custom Swagger UI with proper OAuth2 PKCE configuration
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
        swagger_ui_parameters={
            "persistAuthorization": True,
        },
        init_oauth={
            "clientId": CLIENT_ID,
            "scopes": f"api://{CLIENT_ID}/access_as_user openid profile email",
        }
    )

@app.get(app.swagger_ui_oauth2_redirect_url or "/oauth2-redirect", include_in_schema=False)
async def swagger_ui_redirect():
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""
    <!doctype html>
    <html lang="en-US">
    <head>
        <title>Swagger UI: OAuth2 Redirect</title>
    </head>
    <body>
    <script>
        'use strict';
        function run () {
            var oauth2 = window.opener.swaggerUIRedirectOauth2;
            var sentState = oauth2.state;
            var redirectUrl = oauth2.redirectUrl;
            var isValid, qp, arr;

            if (/code|token|error/.test(window.location.hash)) {
                qp = window.location.hash.substring(1).replace('?', '&');
            } else {
                qp = location.search.substring(1);
            }

            arr = qp.split("&");
            arr.forEach(function (v,i,_arr) { _arr[i] = '"' + v.replace('=', '":"') + '"';});
            qp = qp ? JSON.parse('{' + arr.join() + '}',
                    function (key, value) {
                        return key === "" ? value : decodeURIComponent(value);
                    }
            ) : {};

            isValid = qp.state === sentState;

            if ((
              oauth2.auth.schema.get("flow") === "accessCode" ||
              oauth2.auth.schema.get("flow") === "authorizationCode" ||
              oauth2.auth.schema.get("flow") === "authorization_code"
            ) && !oauth2.auth.code) {
                if (!isValid) {
                    oauth2.errCb({
                        authId: oauth2.auth.name,
                        source: "auth",
                        level: "warning",
                        message: "Authorization may be unsafe, passed state was changed in server. The passed state wasn't returned from auth server."
                    });
                }

                if (qp.code) {
                    delete oauth2.state;
                    oauth2.auth.code = qp.code;
                    oauth2.callback({auth: oauth2.auth, redirectUrl: redirectUrl});
                } else {
                    let oauthErrorMsg;
                    if (qp.error) {
                        oauthErrorMsg = "["+qp.error+"]: " +
                            (qp.error_description ? qp.error_description+ ". " : "no accessCode received from the server. ") +
                            (qp.error_uri ? "More info: "+qp.error_uri : "");
                    }

                    oauth2.errCb({
                        authId: oauth2.auth.name,
                        source: "auth",
                        level: "error",
                        message: oauthErrorMsg || "[Authorization failed]: no accessCode received from the server."
                    });
                }
            } else {
                oauth2.callback({auth: oauth2.auth, token: qp, isValid: isValid, redirectUrl: redirectUrl});
            }
            window.close();
        }

        if (document.readyState !== 'loading') {
            run();
        } else {
            document.addEventListener('DOMContentLoaded', function () {
                run();
            });
        }
    </script>
    </body>
    </html>
    """)

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    """Health check endpoint"""
    return {"status": "healthy", "service": "Award Nomination System"}


@app.get("/whoami")
def whoami(_claims=Depends(require_role("AWard_Nomination_Admin"))):
    """Diagnostic endpoint for Azure Front Door routing (AWard_Nomination_Admin only)"""
    return {
        "region": os.getenv("REGION", "unknown"),
        "container_app": os.getenv("CONTAINER_APP_NAME", "unknown"),
        "revision": os.getenv("CONTAINER_APP_REVISION", "unknown"),
        "hostname": socket.gethostname(),
    }

@app.get("/api/tenant/config")
async def get_tenant_config(user_context: dict = Depends(get_current_user_with_impersonation)):
    """
    Return the per-tenant UI configuration (locale, currency, theme).
    Returns an empty object when no config has been set; frontend falls back
    to hardcoded defaults and logs a warning of its own.
    """
    import json as _json
    actual_user = user_context["actual_user"]
    tenant_id   = actual_user["TenantId"]
    upn         = actual_user.get("userPrincipalName", "unknown")

    logger.debug(
        "tenant_config: fetching config for tenant_id=%d upn=%s",
        tenant_id, upn,
    )

    try:
        raw = sqlhelper.get_tenant_config(tenant_id)
    except Exception as exc:
        logger.error(
            "tenant_config: DB error retrieving config for tenant_id=%d — %s. "
            "Returning empty config; frontend will use defaults.",
            tenant_id, exc,
        )
        return {}

    if raw is None:
        logger.warning(
            "tenant_config: no Config row found for tenant_id=%d (NULL or missing). "
            "Returning empty config; frontend will use defaults.",
            tenant_id,
        )
        return {}

    try:
        parsed = _json.loads(raw)
        logger.debug(
            "tenant_config: returning config for tenant_id=%d — "
            "locale=%s currency=%s primaryColor=%s",
            tenant_id,
            parsed.get("locale",             "?"),
            parsed.get("currency",           "?"),
            parsed.get("theme", {}).get("primaryColor", "?"),
        )

        # Inject the tenant's canonical domain so the frontend can redirect
        # users who land on the wrong hostname before they interact with the app.
        domain = sqlhelper.get_tenant_domain(tenant_id)
        if domain:
            parsed["domain"] = domain

        return parsed
    except Exception as exc:
        logger.error(
            "tenant_config: invalid JSON in Config column for tenant_id=%d — %s. "
            "Returning empty config; frontend will use defaults.",
            tenant_id, exc,
        )
        return {}


@app.get("/api/users", response_model=List[User])
async def get_users(user_context: dict = Depends(get_current_user_with_impersonation)):
    """Get all users for nomination selection"""
    effective_user = user_context["effective_user"]
    tenant_id      = effective_user["TenantId"]

    rows = sqlhelper.get_all_users_except(effective_user["UserId"], tenant_id)
    
    users = []
    for row in rows:
        users.append(User(
            UserId=row[0],
            userPrincipalName=row[1],
            FirstName=row[2],
            LastName=row[3],
            Title=row[4],
            ManagerId=row[5]
        ))
    
    await log_action_if_impersonating(user_context, "viewed_users")
    return users


@app.post("/api/nominations", status_code=status.HTTP_201_CREATED, response_model=StatusResponse)
async def create_nomination(
    nomination: NominationCreate,
    user_context: dict = Depends(get_current_user_with_impersonation)
):
    """Create a new nomination"""
    effective_user = user_context["effective_user"]
    tenant_id      = effective_user["TenantId"]

    # Use structured logging
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
            'NominationDate': datetime.now()
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

    # Log fraud assessment
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
   
    
    # Optionally block high-risk nominations
    if fraud_result['risk_level'] == 'CRITICAL':
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Nomination blocked due to fraud risk: "
                f"{', '.join(fraud_result['warning_flags'])}"
        )
    
    # Insert nomination using effective_user
    # Resolve the tenant's currency from the DB config (server-authoritative)
    import json as _json
    _raw_cfg = sqlhelper.get_tenant_config(tenant_id)
    _currency = "USD"
    if _raw_cfg:
        try:
            _currency = _json.loads(_raw_cfg).get("currency", "USD")
        except Exception:
            pass

    nomination_id = sqlhelper.create_nomination(
        nominator_id=effective_user["UserId"],
        beneficiary_id=nomination.BeneficiaryId,
        approver_id=manager_id,
        amount=nomination.Amount,
        currency=_currency,
        description=nomination.NominationDescription
    )

    logger.info(
        "Nomination created successfully",
        extra={
            "nomination_id": nomination_id,
            "user_id": effective_user["UserId"]
        }
    )

    # Persist the fraud assessment so it feeds future model retraining
    # and populates the analytics fraud dashboard.
    try:
        sqlhelper.save_fraud_assessment(
            nomination_id=nomination_id,
            fraud_score=fraud_result['fraud_score'],
            risk_level=fraud_result['risk_level'],
            warning_flags=", ".join(fraud_result.get('warning_flags', [])),
        )
    except Exception as save_exc:
        # Non-fatal — nomination is already created; just log and continue.
        logger.error(
            "Failed to save fraud assessment for nomination %d: %s",
            nomination_id, save_exc
        )
    
    # Log if impersonating
    await log_action_if_impersonating(
        user_context,
        "created_nomination",
        f"NominationId: {nomination_id}, Beneficiary: {beneficiary_name}, Amount: {nomination.Amount} {_currency}"
    )
    
    # Send email to manager using template
    nominator_name = f"{effective_user['FirstName']} {effective_user['LastName']}"
    
    # Generate secure action URLs
    approve_url = get_action_url(
        os.getenv("API_BASE_URL", "https://award-api-eastus.lemonpond-a2daba01.eastus.azurecontainerapps.io"),
        int(nomination_id),
        "approve",
        int(manager_id)
    )
    
    reject_url = get_action_url(
        os.getenv("API_BASE_URL", "https://award-nomination-api-bqb8ftbdfpemfyck.z02.azurefd.net"),
        int(nomination_id),
        "reject",
        int(manager_id)
    )

    # Use new template with action buttons
    email_body = get_nomination_pending_email(
        manager_name=manager_name,
        nominator_name=f"{effective_user['FirstName']} {effective_user['LastName']}",
        beneficiary_name=beneficiary_name,
        dollar_amount=float(nomination.Amount),
        description=nomination.NominationDescription,
        approve_url=approve_url,
        reject_url=reject_url
    )

    manager_email = manager[2]  # Use userEmail from manager data
    # Send email notification
    try:
        email_sent = await send_email(
            to_email=manager_email,
            subject=f"Award Nomination Pending Approval - {beneficiary_name}",
            body=email_body
        )
        if not email_sent:
            logger.warning(f"⚠️ Failed to send email to {manager_email}")            
            # Don't fail the nomination if email fails - just log it
    except Exception as e:
        logger.warning(f"⚠️ Email send error: {e}")        
        # Don't fail the nomination if email fails
    
    return StatusResponse(
        Status="Pending",
        Message="Nomination submitted successfully"
    )


@app.get("/api/nominations/pending", response_model=List[Nomination])
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
            Status=row[10]
        ))

    await log_action_if_impersonating(user_context, "viewed_pending_approvals")
    return nominations


@app.post("/api/nominations/approve", response_model=StatusResponse)
async def approve_nomination(
    approval: NominationApproval,
    user_context: dict = Depends(get_current_user_with_impersonation)
):
    """Approve or reject a nomination"""
    effective_user = user_context["effective_user"]
    tenant_id      = effective_user["TenantId"]

    # Verify user is the approver — scoped to tenant to block cross-tenant manipulation
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
        # Approve nomination
        sqlhelper.approve_nomination(approval.NominationId)
        
        # Get nomination details for email
        nom_details = sqlhelper.get_nomination_details(approval.NominationId)
        if nom_details:
            nominator_email = nom_details.get('nominator_email')
            beneficiary_name = nom_details.get('beneficiary_name')
            award_amount = nom_details.get('dollar_amount')
            
            # Send approval email to nominator
            if nominator_email:
                try:
                    approval_body = get_nomination_approved_email(
                        beneficiary_name or "the nominee",
                        f"Monetary Award (${award_amount})"
                    )
                    await send_email(
                        to_email=nominator_email,
                        subject=f"✅ Nomination Approved - {beneficiary_name}",
                        body=approval_body
                    )
                except Exception as e:
                    logger.warning(f"⚠️ Failed to send approval email: {e}")                    
        
        # Log if impersonating
        await log_action_if_impersonating(
            user_context,
            "approved_nomination",
            f"NominationId: {approval.NominationId}"
        )
        
        # Generate payroll extract file
        await generate_payroll_extract(approval.NominationId)
        
        return StatusResponse(
            Status="Approved",
            Message="Nomination approved successfully"
        )
    else:
        # Reject nomination
        sqlhelper.reject_nomination(approval.NominationId)
        
        # Get nomination details for email
        nom_details = sqlhelper.get_nomination_details(approval.NominationId)
        if nom_details:
            nominator_email = nom_details.get('nominator_email')
            beneficiary_name = nom_details.get('beneficiary_name')
            award_amount = nom_details.get('dollar_amount')
            
            # Send rejection email to nominator
            if nominator_email:
                try:
                    rejection_body = f"""
                    <html>
                    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                        <h2 style="color: #e74c3c;">Nomination Status Update</h2>
                        <p>Your nomination has been reviewed:</p>
                        <ul>
                            <li><strong>Nominee:</strong> {beneficiary_name}</li>
                            <li><strong>Award Amount:</strong> ${award_amount}</li>
                            <li><strong>Status:</strong> <span style="color: #e74c3c;">Not Approved</span></li>
                        </ul>
                        <p>Thank you for your participation in the award nomination process.</p>
                        <hr style="margin: 20px 0;">
                        <p style="color: #7f8c8d; font-size: 12px;">
                            This is an automated message from the Award Nomination System.
                        </p>
                    </body>
                    </html>
                    """
                    await send_email(
                        to_email=nominator_email,
                        subject=f"Nomination Status - {beneficiary_name}",
                        body=rejection_body
                    )
                except Exception as e:
                    logger.warning(f"⚠️ Failed to send rejection email: {e}")                    
        
        # Log if impersonating
        await log_action_if_impersonating(
            user_context,
            "rejected_nomination",
            f"NominationId: {approval.NominationId}"
        )
        
        return StatusResponse(
            Status="Rejected",
            Message="Nomination rejected"
        )


@app.get("/api/nominations/history", response_model=List[Nomination])
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
            Status=row[10]
        ))

    await log_action_if_impersonating(user_context, "viewed_nomination_history")
    return nominations


@app.get("/api/admin/audit-logs", response_model=List[AuditLog])
async def get_audit_logs(
    limit: int = 100,
    current_user: dict = Depends(get_current_user)  # No impersonation for admin endpoints
):
    """Get impersonation audit logs (AWard_Nomination_Admin only)"""
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AWard_Nomination_Admin access required"
        )
    
    rows = sqlhelper.get_audit_logs(limit)
    
    logs = []
    for row in rows:
        logs.append(AuditLog(
            AuditId=row[0],
            Timestamp=row[1],
            AdminUPN=row[2],
            ImpersonatedUPN=row[3],
            Action=row[4],
            Details=row[5],
            IpAddress=row[6]
        ))
    
    return logs


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok")

@app.post("/api/admin/refresh-fraud-model")
async def refresh_fraud_model(current_user: dict = Depends(require_role("AWard_Nomination_Admin"))):
    """
    Manually refresh the fraud detection model from Azure Blob Storage (Admin only)
    
    Checks if there's a newer version in blob storage and downloads it if available.
    """
    import fraud_ml
    
    try:
        updated = fraud_ml.refresh_model()

        tenant_summaries = {
            tid: str(m['training_date']) if m else "not loaded"
            for tid, m in fraud_ml.fraud_detector.tenant_models.items()
        }

        return {
            "status": "success",
            "message": "Fraud detection models updated successfully" if updated else "Models already up to date",
            "updated": updated,
            "tenant_models": tenant_summaries
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to refresh model: {str(e)}"
        )


@app.get("/api/admin/fraud-model-info")
async def get_fraud_model_info(current_user: dict = Depends(require_role("AWard_Nomination_Admin"))):
    """
    Get information about the currently loaded fraud detection model (Admin only)
    """
    import fraud_ml
    
    tenant_models = fraud_ml.fraud_detector.tenant_models
    if not any(m is not None for m in tenant_models.values()):
        return {
            "status": "not_loaded",
            "message": "No fraud detection models are loaded"
        }

    return {
        "status": "loaded",
        "tenant_models": {
            tid: {
                "model_trained":  str(m['training_date']),
                "training_samples": m.get('training_samples'),
                "auc":            m.get('auc'),
                "feature_count":  len(m['feature_columns']),
                "features":       m['feature_columns']
            } if m else {"status": "not_loaded"}
            for tid, m in tenant_models.items()
        }
    }

@app.get("/api/nominations/email-action", response_class=HTMLResponse)
async def handle_email_action(token: str = Query(..., description="Action token from email")):
    """
    🔗 Handle approve/reject action from email button click
    
    This endpoint:
    1. Verifies the token is valid and not expired
    2. Checks the user is authorized to approve/reject
    3. Performs the action
    4. Shows a confirmation page in the browser
    
    Args:
        token: JWT token from email link
    
    Returns:
        HTML page with success/error message
    
    Security:
        - Token must be valid and not expired (72 hours)
        - Token contains approver_id which is verified against DB
        - Token is signed with secret key (cannot be forged)
    """
    
    # 1️⃣ Verify and decode token
    payload = verify_action_token(token)
    
    if not payload:
        return get_action_confirmation_page(
            action="",
            success=False,
            message="This link has expired or is invalid. Please log in to the Award Nomination System to approve or reject this nomination."
        )
    
    nomination_id = payload["nomination_id"]
    action = payload["action"]  # "approve" or "reject"
    expected_approver_id = payload["approver_id"]
    
    # 2️⃣ Verify nomination exists and user is the approver
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
    
    # 3️⃣ Check if already processed
    nomination_status = sqlhelper.get_nomination_status(nomination_id)
    if nomination_status in ["Approved", "Rejected"]:
        return get_action_confirmation_page(
            action=nomination_status.lower(),
            success=True,
            message=f"This nomination has already been {nomination_status.lower()}."
        )
    
    # 4️⃣ Perform the action
    try:
        if action == "approve":
            sqlhelper.approve_nomination(nomination_id)
            
            # Get nomination details for email
            nom_details = sqlhelper.get_nomination_details(nomination_id)
            if nom_details:
                nominator_email = nom_details.get('nominator_email')
                beneficiary_name = nom_details.get('beneficiary_name')
                award_amount = nom_details.get('dollar_amount')
                
                # Send approval email to nominator
                if nominator_email:
                    try:
                        from email_utils import send_email, get_nomination_approved_email
                        approval_body = get_nomination_approved_email(
                            beneficiary_name or "the nominee",
                            f"Monetary Award (${award_amount})"
                        )
                        await send_email(
                            to_email=nominator_email,
                            subject=f"✅ Nomination Approved - {beneficiary_name}",
                            body=approval_body
                        )
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to send approval email: {e}")                        
            
            # Generate payroll extract
            await generate_payroll_extract(nomination_id)
            
            return get_action_confirmation_page(
                action="approved",
                success=True,
                message=f"The nomination has been approved successfully. The nominator has been notified via email."
            )
            
        else:  # action == "reject"
            sqlhelper.reject_nomination(nomination_id)
            
            # Get nomination details for email
            nom_details = sqlhelper.get_nomination_details(nomination_id)
            if nom_details:
                nominator_email = nom_details.get('nominator_email')
                beneficiary_name = nom_details.get('beneficiary_name')
                award_amount = nom_details.get('dollar_amount')
                
                # Send rejection email to nominator
                if nominator_email:
                    try:
                        from email_utils import send_email
                        rejection_body = f"""
                        <html>
                        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                            <h2 style="color: #e74c3c;">Nomination Status Update</h2>
                            <p>Your nomination has been reviewed:</p>
                            <ul>
                                <li><strong>Nominee:</strong> {beneficiary_name}</li>
                                <li><strong>Award Amount:</strong> ${award_amount}</li>
                                <li><strong>Status:</strong> <span style="color: #e74c3c;">Not Approved</span></li>
                            </ul>
                            <p>Thank you for your participation in the award nomination process.</p>
                            <hr style="margin: 20px 0;">
                            <p style="color: #7f8c8d; font-size: 12px;">
                                This is an automated message from the Award Nomination System.
                            </p>
                        </body>
                        </html>
                        """
                        await send_email(
                            to_email=nominator_email,
                            subject=f"Nomination Status - {beneficiary_name}",
                            body=rejection_body
                        )
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to send rejection email: {e}")                        
            
            return get_action_confirmation_page(
                action="rejected",
                success=True,
                message=f"The nomination has been rejected. The nominator has been notified via email."
            )
            
    except Exception as e:
        logger.error(f"❌ Error processing email action: {e}")        
        return get_action_confirmation_page(
            action="",
            success=False,
            message=f"An error occurred while processing your request: {str(e)}"
        )


# ============================================================================
# PAYROLL EXTRACT GENERATION
# ============================================================================

async def generate_payroll_extract(nomination_id: int):
    """Generate payroll extract file for approved nomination"""
    row = sqlhelper.get_nomination_for_payroll(nomination_id)
    
    if row:
        # Generate CSV file for payroll system
        extract_filename = f"payroll_extract_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        with open(extract_filename, 'w') as f:
            f.write("EmployeeId,FirstName,LastName,AwardAmount,Date\n")
            f.write(f"{row[0]},{row[3]},{row[4]},{row[1]},{row[2]}\n")
        
        # Update PayedDate
        sqlhelper.mark_nomination_as_paid(nomination_id)
        
        logger.info(f"Payroll extract generated: {extract_filename}")
        
        # In production, upload to Azure Blob Storage or SFTP to payroll system


# ============================================================================
# ANALYTICS ENDPOINTS (Admin Only)
# ============================================================================

@app.get("/api/admin/analytics/overview")
async def get_analytics_overview(
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get high-level analytics overview"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        metrics = sqlhelper.get_analytics_overview(tenant_id)
        return {
            'totalNominationsAllTime': metrics.get('totalNominations', 0),
            'totalAmountSpent': metrics.get('totalAmount', 0),
            'approvedNominations': metrics.get('approvedCount', 0),
            'pendingNominations': metrics.get('pendingCount', 0),
            'averageAwardAmount': metrics.get('avgAmount', 0),
            'rejectionRate': metrics.get('rejectionRate', 0),
            'fraudAlertsThisMonth': len(sqlhelper.get_fraud_alerts(tenant_id, limit=100))
        }
    except Exception as e:
        logger.error(f"Error fetching analytics overview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/analytics/spending-trends")
async def get_spending_trends(
    days: int = Query(default=90, ge=1, le=365),
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get spending trends over time"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        trends = sqlhelper.get_spending_trends(tenant_id, days=days)
        return [
            {
                'date': row[0].isoformat(),
                'nominationCount': row[1],
                'amount': row[2]
            }
            for row in trends
        ]
    except Exception as e:
        logger.error(f"Error fetching spending trends: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/analytics/department-spending")
async def get_department_spending(
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get spending breakdown by department"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        departments = sqlhelper.get_department_spending(tenant_id)
        return [
            {
                'departmentName': row[0] or 'Unknown',
                'nominationCount': row[1],
                'totalSpent': row[2],
                'averageAmount': row[3]
            }
            for row in departments
        ]
    except Exception as e:
        logger.error(f"Error fetching department spending: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/analytics/top-recipients")
async def get_top_recipients(
    limit: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get top award recipients"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        recipients = sqlhelper.get_top_recipients(tenant_id, limit=limit)
        return [
            {
                'UserId': row[0],
                'FirstName': row[1],
                'LastName': row[2],
                'nominationCount': row[3],
                'totalAmount': row[4]
            }
            for row in recipients
        ]
    except Exception as e:
        logger.error(f"Error fetching top recipients: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/analytics/top-nominators")
async def get_top_nominators(
    limit: int = Query(default=10, ge=1, le=50),
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get top nominators"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        nominators = sqlhelper.get_top_nominators(tenant_id, limit=limit)
        return [
            {
                'UserId': row[0],
                'FirstName': row[1],
                'LastName': row[2],
                'nominationCount': row[3],
                'totalAmount': row[4]
            }
            for row in nominators
        ]
    except Exception as e:
        logger.error(f"Error fetching top nominators: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/analytics/fraud-alerts")
async def get_fraud_alerts(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get recent fraud detection alerts"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        alerts = sqlhelper.get_fraud_alerts(tenant_id, limit=limit)
        return [
            {
                'NominationId': row[0],
                'fraudScore': row[1],
                'riskLevel': row[2],
                'flags': row[3].split(',') if row[3] else [],
                'nominatorName': f"{row[4]} {row[5]}",
                'beneficiaryName': f"{row[6]} {row[7]}",
                'amount': row[8],
                'nominationDate': row[9].isoformat()
            }
            for row in alerts
        ]
    except Exception as e:
        logger.error(f"Error fetching fraud alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/analytics/approval-metrics")
async def get_approval_metrics(
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get approval and rejection metrics"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        metrics = sqlhelper.get_approval_metrics(tenant_id)
        return metrics
    except Exception as e:
        logger.error(f"Error fetching approval metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/analytics/diversity-metrics")
async def get_diversity_metrics(
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Get award distribution diversity metrics"""
    tenant_id = current_user["effective_user"]["TenantId"]
    try:
        metrics = sqlhelper.get_diversity_metrics(tenant_id)
        return metrics
    except Exception as e:
        logger.error(f"Error fetching diversity metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# ============================================================================
# AI ANALYTICS — ASK ENDPOINT
# ============================================================================

from agents import AskAgent, AskResult

class AnalyticsQuestion(BaseModel):
    question: str

_ask_agent = AskAgent()   # shared, stateless

@app.post("/api/admin/analytics/ask")
async def ask_analytics_question(
    req: AnalyticsQuestion,
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Ask an AI-powered question about analytics data."""
    actual_user = current_user["actual_user"]
    tenant_id   = actual_user["TenantId"]
    logger.info("ask endpoint: %s (tenant_id=%d)", req.question[:80], tenant_id)

    result: AskResult = await _ask_agent.ask(
        req.question,
        tenant_id    = tenant_id,
        current_user = actual_user,
    )

    if result.error:
        logger.error("ask endpoint: agent returned error: %s", result.error)
        raise HTTPException(status_code=500, detail=f"AI Service Error: {result.error}")

    logger.info(
        "ask endpoint: answered (sql=%s, rows=%d)",
        bool(result.sql), result.rows_fetched,
    )

    response : dict[str, Any]= {
        "question": result.question,
        "answer":   result.answer,
    }

    if result.export_path:
        fmt = (result.export_format or "file").upper()
        filename = result.export_path.split("?")[0].split("/")[-1]
        response["export"] = {
            "format":       result.export_format,
            "file_size":    result.export_size,
            "label":        f"Download your {fmt} here",
            "filename":     filename,
            "download_url": result.export_path,
        }

    logger.info("ask endpoint: export_path=%s, export_format=%s", result.export_path, result.export_format)

    return response


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)