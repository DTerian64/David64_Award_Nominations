# Award Nomination System - FastAPI Application
# Architecture: FastAPI + Azure SQL + Entra ID + Email Notifications

from dotenv import load_dotenv
load_dotenv()

import os
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from datetime import datetime
import jwt
import httpx

import sqlhelper
from models import (
    User, NominationCreate, Nomination, NominationApproval,
    StatusResponse, HealthResponse
)


# ============================================================================
# CONFIGURATION
# ============================================================================

# Microsoft Entra ID Configuration
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
JWKS_URL = f"{AUTHORITY}/discovery/v2.0/keys"

# Email Configuration (SendGrid)
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL")


# ============================================================================
# FASTAPI APPLICATION
# ============================================================================

from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi

app = FastAPI(
    title="Award Nomination System",
    description="Employee recognition and monetary award nomination system",
    version="1.0.0",
    docs_url=None,  # Disable default docs
    redoc_url=None,
)

# Custom Swagger UI with proper OAuth2 PKCE configuration
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
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
            # Using implicit flow - no client secret needed
        }
    )

@app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# AUTHENTICATION
# ============================================================================

from fastapi.security import OAuth2

# Custom OAuth2 scheme for Azure AD implicit flow (works with Swagger UI)
oauth2_scheme = OAuth2(
    flows={
        "implicit": {
            "authorizationUrl": f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize",
            "scopes": {
                f"api://{CLIENT_ID}/access_as_user": "Access the API as the signed-in user",
                "openid": "OpenID Connect",
                "profile": "User profile",
                "email": "User email",
            }
        }
    }
)


async def get_current_user(token: str = Depends(oauth2_scheme)):
    """Validate Microsoft Entra ID token and return user info"""
    
    try:
        # Remove "Bearer " prefix if present
        if token.startswith("Bearer "):
            token = token[7:]  # Remove "Bearer " (7 characters)
        
        # Decode without verification first to see the payload
        payload = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_iss": False,
                "verify_exp": False
            }
        )
        
        # Debug: Print the token payload to see what claims we have
        print("Token payload claims:", list(payload.keys()))
        
        # Get User Principal Name from token
        # Azure AD can use different claims: upn, preferred_username, email
        upn = payload.get("upn") or payload.get("preferred_username") or payload.get("email")
        
        if not upn:
            print(f"UPN not found. Available claims: {list(payload.keys())}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"User Principal Name not found in token. Available claims: {list(payload.keys())}"
            )
        
        print(f"Found UPN: {upn}")
        
        # Get user from database by UPN
        row = sqlhelper.get_user_by_upn(upn)
        
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User not found in system with UPN: {upn}"
            )
        
        print(f"User found: {row[2]} {row[3]} (ID: {row[0]})")
        
        return {
            "UserId": row[0],
            "userPrincipalName": row[1],
            "FirstName": row[2],
            "LastName": row[3],
            "Title": row[4],
            "ManagerId": row[5]
        }
    
    except jwt.DecodeError as e:
        print(f"JWT Decode Error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token format: {str(e)}"
        )
    except jwt.InvalidTokenError as e:
        print(f"Invalid Token Error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}"
        )
    except Exception as e:
        print(f"Error in get_current_user: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Authentication error: {str(e)}"
        )


# ============================================================================
# EMAIL NOTIFICATION SERVICE
# ============================================================================

async def send_email(to_email: str, subject: str, body: str):
    """Send email notification using SendGrid"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {SENDGRID_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "personalizations": [{"to": [{"email": to_email}]}],
                    "from": {"email": FROM_EMAIL},
                    "subject": subject,
                    "content": [{"type": "text/html", "value": body}]
                }
            )
            return response.status_code == 202
    except Exception as e:
        print(f"Email error: {e}")
        return False


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    """Health check endpoint"""
    return {"status": "healthy", "service": "Award Nomination System"}


@app.get("/api/users", response_model=List[User])
async def get_users(current_user: dict = Depends(get_current_user)):
    """Get all users for nomination selection"""
    rows = sqlhelper.get_all_users_except(current_user["UserId"])
    
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
    
    return users


@app.post("/api/nominations", status_code=status.HTTP_201_CREATED, response_model=StatusResponse)
async def create_nomination(
    nomination: NominationCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a new award nomination"""
    
    # Get beneficiary details
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
    manager_name = f"{manager[0]} {manager[1]}"
    
    # Insert nomination
    nomination_id = sqlhelper.create_nomination(
        nominator_id=current_user["UserId"],
        beneficiary_id=nomination.BeneficiaryId,
        approver_id=manager_id,
        dollar_amount=nomination.DollarAmount,
        description=nomination.NominationDescription
    )
    
    # Send email to manager
    nominator_name = f"{current_user['FirstName']} {current_user['LastName']}"
    email_body = f"""
    <html>
    <body>
        <h2>New Award Nomination Pending Approval</h2>
        <p>Dear {manager_name},</p>
        <p><strong>{nominator_name}</strong> has nominated <strong>{beneficiary_name}</strong> 
        for a monetary award of <strong>${nomination.DollarAmount}</strong>.</p>
        
        <h3>Nomination Details:</h3>
        <p>{nomination.NominationDescription}</p>
        
        <p>Please review and approve/reject this nomination in the Award Nomination System.</p>
        <p><a href="https://yourapp.azurewebsites.net/nominations/{nomination_id}">
        View Nomination</a></p>
    </body>
    </html>
    """
    
    # In production, get manager email from Azure AD or Users table
    manager_email = f"{manager[1].lower()}.{manager[0].lower()}@yourcompany.com"
    await send_email(
        to_email=manager_email,
        subject=f"Award Nomination Pending Approval - {beneficiary_name}",
        body=email_body
    )
    
    return StatusResponse(
        Status="Pending",
        Message="Nomination submitted successfully"
    )


@app.get("/api/nominations/pending", response_model=List[Nomination])
async def get_pending_nominations(current_user: dict = Depends(get_current_user)):
    """Get nominations pending approval for current user (as manager)"""
    rows = sqlhelper.get_pending_nominations_for_approver(current_user["UserId"])
    
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
            Status="Pending"
        ))
    
    return nominations


@app.post("/api/nominations/approve", response_model=StatusResponse)
async def approve_nomination(
    approval: NominationApproval,
    current_user: dict = Depends(get_current_user)
):
    """Approve or reject a nomination"""
    
    # Verify user is the approver
    approver_id = sqlhelper.get_nomination_approver(approval.NominationId)
    
    if approver_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nomination not found"
        )
    
    if approver_id != current_user["UserId"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to approve this nomination"
        )
    
    if approval.Approved:
        # Approve nomination
        sqlhelper.approve_nomination(approval.NominationId)
        
        # Generate payroll extract file
        await generate_payroll_extract(approval.NominationId)
        
        return StatusResponse(
            Status="Approved",
            Message="Nomination approved successfully"
        )
    else:
        # Reject nomination
        sqlhelper.reject_nomination(approval.NominationId)
        
        return StatusResponse(
            Status="Rejected",
            Message="Nomination rejected"
        )


@app.get("/api/nominations/history", response_model=List[Nomination])
async def get_nomination_history(current_user: dict = Depends(get_current_user)):
    """Get nomination history for current user"""
    rows = sqlhelper.get_nomination_history(current_user["UserId"])
    
    nominations = []
    for row in rows:
        status = "Pending"
        if row[8]:  # PayedDate
            status = "Paid"
        elif row[7]:  # ApprovedDate
            status = "Approved"
        
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
            Status=status
        ))
    
    return nominations


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok")


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
        
        print(f"Payroll extract generated: {extract_filename}")
        
        # In production, upload to Azure Blob Storage or SFTP to payroll system


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)