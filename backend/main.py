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
from fastapi import FastAPI, Depends, HTTPException, status,HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List
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

import sqlhelper
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

from sql_agent import generate_sql


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

app = FastAPI(
    title="Award Nomination System",
    description="Employee recognition and monetary award nomination system",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)

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

# CORS Configuration
ALLOWED_ORIGINS = ["https://awards.terian-services.com"]

# Add development origins if needed
if os.getenv("ENVIRONMENT", "production") == "development":
    ALLOWED_ORIGINS.extend([
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ])


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,              # ✅ CRITICAL: Must be True for auth
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],                # ✅ Added: Expose response headers
    max_age=3600,                        # ✅ Added: Cache preflight for 1 hour
)

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

@app.get("/api/users", response_model=List[User])
async def get_users(user_context: dict = Depends(get_current_user_with_impersonation)):
    """Get all users for nomination selection"""
    effective_user = user_context["effective_user"]
   
    rows = sqlhelper.get_all_users_except(effective_user["UserId"])
    
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

    # Use structured logging
    logger.info(
        "Nomination submission started",
        extra={
            "user_id": effective_user["UserId"],
            "beneficiary_id": nomination.BeneficiaryId,
            "amount": float(nomination.DollarAmount)
        }
    )
    
    # Get beneficiary's manager
    beneficiary = sqlhelper.get_user_manager_info(nomination.BeneficiaryId)
    
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
    fraud_result = fraud_ml.get_fraud_assessment({
        'NominatorId': effective_user["UserId"],
        'BeneficiaryId': nomination.BeneficiaryId,
        'ApproverId': manager_id,
        'DollarAmount': nomination.DollarAmount,
        'NominationDate': datetime.now()
    })

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
    nomination_id = sqlhelper.create_nomination(
        nominator_id=effective_user["UserId"],
        beneficiary_id=nomination.BeneficiaryId,
        approver_id=manager_id,
        dollar_amount=nomination.DollarAmount,
        description=nomination.NominationDescription
    )

    logger.info(
        "Nomination created successfully",
        extra={
            "nomination_id": nomination_id,
            "user_id": effective_user["UserId"]
        }
    )
    
    # Log if impersonating
    await log_action_if_impersonating(
        user_context,
        "created_nomination",
        f"NominationId: {nomination_id}, Beneficiary: {beneficiary_name}, Amount: ${nomination.DollarAmount}"
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
        dollar_amount=float(nomination.DollarAmount),
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
    
    rows = sqlhelper.get_pending_nominations_for_approver(effective_user["UserId"])
    
    nominations = []
    for row in rows:
        nominations.append(Nomination(
            NominationId=row[0],
            NominatorId=row[1],
            BeneficiaryId=row[2],
            ApproverId=row[3],
            DollarAmount=row[4],
            NominationDescription=row[5],
            NominationDate=row[6],
            ApprovedDate=row[7],
            PayedDate=row[8],
            Status=row[9]  # Now reading Status from database
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
    
    # Verify user is the approver
    approver_id = sqlhelper.get_nomination_approver(approval.NominationId)
    
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
    
    rows = sqlhelper.get_nomination_history(effective_user["UserId"])
    
    nominations = []
    for row in rows:
        nominations.append(Nomination(
            NominationId=row[0],
            NominatorId=row[1],
            BeneficiaryId=row[2],
            ApproverId=row[3],
            DollarAmount=row[4],
            NominationDescription=row[5],
            NominationDate=row[6],
            ApprovedDate=row[7],
            PayedDate=row[8],
            Status=row[9]  # Now reading Status from database
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
        
        if updated:
            return {
                "status": "success",
                "message": "Fraud detection model updated successfully",
                "model_trained": str(fraud_ml.fraud_detector.training_date),
                "updated": True
            }
        else:
            return {
                "status": "success",
                "message": "Model is already up to date",
                "model_trained": str(fraud_ml.fraud_detector.training_date),
                "updated": False
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
    
    if fraud_ml.fraud_detector.model is None:
        return {
            "status": "not_loaded",
            "message": "Fraud detection model is not loaded"
        }
    
    return {
        "status": "loaded",
        "model_trained": str(fraud_ml.fraud_detector.training_date),
        "feature_count": len(fraud_ml.fraud_detector.feature_columns),
        "features": fraud_ml.fraud_detector.feature_columns
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
    try:
        metrics = sqlhelper.get_analytics_overview()
        return {
            'totalNominationsAllTime': metrics.get('totalNominations', 0),
            'totalAmountSpent': metrics.get('totalAmount', 0),
            'approvedNominations': metrics.get('approvedCount', 0),
            'pendingNominations': metrics.get('pendingCount', 0),
            'averageAwardAmount': metrics.get('avgAmount', 0),
            'rejectionRate': metrics.get('rejectionRate', 0),
            'fraudAlertsThisMonth': len(sqlhelper.get_fraud_alerts(limit=100))
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
    try:
        trends = sqlhelper.get_spending_trends(days=days)
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
    try:
        departments = sqlhelper.get_department_spending()
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
    try:
        recipients = sqlhelper.get_top_recipients(limit=limit)
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
    try:
        nominators = sqlhelper.get_top_nominators(limit=limit)
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
    try:
        alerts = sqlhelper.get_fraud_alerts(limit=limit)
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
    try:
        metrics = sqlhelper.get_approval_metrics()
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
    try:
        metrics = sqlhelper.get_diversity_metrics()
        return metrics
    except Exception as e:
        logger.error(f"Error fetching diversity metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# AI ANALYTICS ENDPOINT
# ============================================================================

class AnalyticsQuestion(BaseModel):
    question: str


@app.post("/api/admin/analytics/ask")
async def ask_analytics_question(
    req: AnalyticsQuestion,
    current_user: User = Depends(get_current_user_with_impersonation),
    _: None = Depends(require_role("AWard_Nomination_Admin"))
):
    """Ask an AI-powered question about analytics data"""
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=os.getenv("AZURE_OPENAI_KEY", ""),            
            base_url=os.getenv("AZURE_OPENAI_ENDPOINT", "")
        )

        logger.info(f"Analytics question received: {req.question[:80]}")

        # ── Step 1: Ask the SQL agent to translate the question ──────────────
        # Returns a T-SQL string if answerable from schema, or None if not.
        sql = generate_sql(client, req.question)

        # ── Step 2: Build the analytics context ──────────────────────────────
        if sql:
            # SQL agent produced a targeted query — fetch only the data we need
            logger.info(f"sql_agent returned {sql} — executing targeted query")
            try:
                rows = sqlhelper.run_query(sql)          # ← one focused query
                analytics_context = f"""
                    TARGETED QUERY RESULT for: "{req.question}"

                    SQL executed:
                    {sql}

                    Results ({len(rows)} rows):
                    {_format_rows(rows)}
                """
            except Exception as query_err:
                # SQL ran but failed (e.g. edge-case schema mismatch) — fall back
                logger.warning(f"sql_agent query failed ({query_err}), falling back to full RAG")
                sql = None

        if not sql:
            # Fallback: original broad data dump for questions outside the schema
            # (e.g. "Is our approval time improving?" — needs trend analysis)
            logger.info("Falling back to full analytics context (RAG)")
            analytics_context = _build_full_analytics_context()

        # ── Step 3: Same LLM call as before ──────────────────────────────────
        deployment = os.getenv("AZURE_OPENAI_MODEL", "gpt-4.1")
        system_prompt = """You are an expert business analyst specializing in employee recognition programs.
                            You have access to award nomination analytics data.
                            Be concise but thorough. Use data to support your responses. Provide recommendations when relevant."""

        user_prompt = f"""{analytics_context}

                        Question: {req.question}

                        Please provide a detailed, data-driven response based on the data provided above."""

        try:
            response = client.chat.completions.create(
                model=deployment,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=1000,
            )
        except Exception as api_err:
            error_str = str(api_err)
            if "404" in error_str or "not found" in error_str.lower():
                logger.error(
                    f"Azure OpenAI deployment '{deployment}' not found. "
                    f"Verify AZURE_OPENAI_MODEL env var matches your actual deployment name in Azure. "
                    f"Error: {api_err}"
                )
            raise

        logger.info(f"Analytics question answered: {req.question[:80]}")

        return {
            "question": req.question,
            "answer":   response.choices[0].message.content,
        }

    except Exception as e:
        logger.error(f"Error answering analytics question: {e}")
        raise HTTPException(status_code=500, detail=f"AI Service Error: {str(e)}")

def _format_rows(rows) -> str:
    """Format raw SQL result rows into readable text for the LLM prompt."""
    if not rows:
        return "  (no results)"
    lines = []
    for i, row in enumerate(rows[:50], 1):   # cap at 50 rows to stay within token budget
        lines.append(f"  Row {i}: {', '.join(str(v) for v in row)}")
    if len(rows) > 50:
        lines.append(f"  ... and {len(rows) - 50} more rows (truncated)")
    return "\n".join(lines)


def _build_full_analytics_context() -> str:
    """Original broad data fetch — used as fallback for open-ended questions."""
    overview            = sqlhelper.get_analytics_overview()
    approval_metrics    = sqlhelper.get_approval_metrics()
    diversity_metrics   = sqlhelper.get_diversity_metrics()
    department_spending = sqlhelper.get_department_spending()
    top_recipients      = sqlhelper.get_top_recipients(limit=5)
    top_nominators      = sqlhelper.get_top_nominators(limit=5)

    ctx = f"""
    AWARD NOMINATION ANALYTICS DATA:

    Overview:
    - Total Nominations: {overview.get('totalNominations', 0)}
    - Total Amount Spent: ${overview.get('totalAmount', 0):,}
    - Approved Nominations: {overview.get('approvedCount', 0)}
    - Pending Nominations: {overview.get('pendingCount', 0)}
    - Average Award Amount: ${overview.get('avgAmount', 0):.2f}
    - Rejection Rate: {(overview.get('rejectionRate', 0) * 100):.1f}%

    Approval Metrics:
    - Total Nominations: {approval_metrics.get('totalNominations', 0)}
    - Approved: {approval_metrics.get('approvedCount', 0)}
    - Rejected: {approval_metrics.get('rejectedCount', 0)}
    - Average Days to Approval: {approval_metrics.get('avgDaysToApproval', 0):.1f}
    - Approval Rate: {(approval_metrics.get('approvalRate', 0) * 100):.1f}%

    Diversity Metrics:
    - Unique Recipients: {diversity_metrics.get('uniqueRecipients', 0)}
    - Gini Coefficient: {diversity_metrics.get('giniCoefficient', 0):.3f}
    - Top Recipient Share: {diversity_metrics.get('topRecipientPercent', 0):.1f}%

    Top Recipients:"""

    for r in top_recipients:
        ctx += f"\n- {r[1]} {r[2]}: {r[3]} awards, ${r[4]:,}"

    ctx += "\n\nTop Nominators:"
    for n in top_nominators:
        ctx += f"\n- {n[1]} {n[2]}: {n[3]} nominations, ${n[4]:,}"

    ctx += "\n\nDepartment Breakdown:"
    for dept in department_spending:
        ctx += f"\n- {dept[0]}: {dept[1]} awards, ${dept[2]:,} total, ${dept[3]:.0f} avg"

    return ctx


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)