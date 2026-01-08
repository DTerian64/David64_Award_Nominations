# Award Nomination System - FastAPI Application
# Architecture: FastAPI + Azure SQL + Entra ID + Email Notifications

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2AuthorizationCodeBearer
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import date, datetime
import pyodbc
import jwt
import httpx
import os
from contextlib import contextmanager

# ============================================================================
# CONFIGURATION
# ============================================================================

# Azure SQL Configuration
SQL_SERVER = os.getenv("SQL_SERVER")
SQL_DATABASE = os.getenv("SQL_DATABASE") # Update with your database name
SQL_USER = os.getenv("SQL_USER")
SQL_PASSWORD = os.getenv("SQL_PASSWORD")  # In production, use Azure Key Vault

# Microsoft Entra ID Configuration
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")  # Register app in Azure Portal
CLIENT_SECRET = os.getenv("CLIENT_SECRET")  # From app registration

# Email Configuration (Azure Communication Services or SendGrid)
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL")

# ============================================================================
# DATABASE CONNECTION
# ============================================================================

CONNECTION_STRING = (
    f"DRIVER={{ODBC Driver 18 for SQL Server}};"
    f"SERVER={SQL_SERVER};"
    f"DATABASE={SQL_DATABASE};"
    f"UID={SQL_USER};"
    f"PWD={SQL_PASSWORD};"
    f"Encrypt=yes;"
    f"TrustServerCertificate=no;"
    f"Connection Timeout=30;"
)

@contextmanager
def get_db_connection():
    """Database connection context manager"""
    conn = pyodbc.connect(CONNECTION_STRING)
    try:
        yield conn
    finally:
        conn.close()

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class User(BaseModel):
    UserId: int
    FirstName: str
    LastName: str
    Title: str
    ManagerId: Optional[int]
    Email: Optional[str]

class NominationCreate(BaseModel):
    BeneficiaryId: int
    DollarAmount: int
    NominationDescription: str

class Nomination(BaseModel):
    NominationId: int
    NominatorId: int
    BeneficiaryId: int
    ApproverId: int
    DollarAmount: int
    NominationDescription: str
    NominationDate: date
    ApprovedDate: Optional[date]
    PayedDate: Optional[date]
    Status: str  # Pending, Approved, Rejected, Paid

class NominationApproval(BaseModel):
    NominationId: int
    Approved: bool
    Comments: Optional[str]

# ============================================================================
# FASTAPI APPLICATION
# ============================================================================

app = FastAPI(
    title="Award Nomination System",
    description="Employee recognition and monetary award nomination system",
    version="1.0.0"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# OAuth2 Configuration
oauth2_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl=f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize",
    tokenUrl=f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
)

# ============================================================================
# AUTHENTICATION
# ============================================================================

async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """Validate Microsoft Entra ID token and return user info"""
    try:
        # Decode JWT token (in production, validate signature with Microsoft's public keys)
        decoded = jwt.decode(token, options={"verify_signature": False})
        
        user_id = decoded.get("oid")  # Object ID from Entra ID
        email = decoded.get("email") or decoded.get("preferred_username")
        
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token"
            )
        
        # Get user from database
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT UserId, FirstName, LastName, Title, ManagerId FROM Users WHERE UserId = ?",
                (user_id,)
            )
            row = cursor.fetchone()
            
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found in system"
                )
            
            return {
                "UserId": row[0],
                "FirstName": row[1],
                "LastName": row[2],
                "Title": row[3],
                "ManagerId": row[4],
                "Email": email
            }
    
    except jwt.DecodeError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format"
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
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT UserId, FirstName, LastName, Title, ManagerId 
            FROM Users 
            WHERE UserId != ?
            ORDER BY LastName, FirstName
        """, (current_user["UserId"],))
        
        users = []
        for row in cursor.fetchall():
            users.append(User(
                UserId=row[0],
                FirstName=row[1],
                LastName=row[2],
                Title=row[3],
                ManagerId=row[4]
            ))
        
        return users

@app.post("/api/nominations", status_code=status.HTTP_201_CREATED)
async def create_nomination(
    nomination: NominationCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a new award nomination"""
    
    # Validate beneficiary exists and get their manager
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Get beneficiary details
        cursor.execute(
            "SELECT ManagerId, FirstName, LastName FROM Users WHERE UserId = ?",
            (nomination.BeneficiaryId,)
        )
        beneficiary = cursor.fetchone()
        
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
        
        # Get manager email
        cursor.execute(
            "SELECT FirstName, LastName FROM Users WHERE UserId = ?",
            (manager_id,)
        )
        manager = cursor.fetchone()
        manager_name = f"{manager[0]} {manager[1]}"
        
        # Insert nomination
        cursor.execute("""
            INSERT INTO Nominations 
            (NominatorId, BeneficiaryId, ApproverId, DollarAmount, 
             NominationDescription, NominationDate, ApprovedDate, PayedDate)
            VALUES (?, ?, ?, ?, ?, GETDATE(), NULL, NULL)
        """, (
            current_user["UserId"],
            nomination.BeneficiaryId,
            manager_id,
            nomination.DollarAmount,
            nomination.NominationDescription
        ))
        
        conn.commit()
        nomination_id = cursor.execute("SELECT @@IDENTITY").fetchone()[0]
        
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
        await send_email(
            to_email=f"{manager[1].lower()}.{manager[0].lower()}@yourcompany.com",
            subject=f"Award Nomination Pending Approval - {beneficiary_name}",
            body=email_body
        )
        
        return {
            "NominationId": nomination_id,
            "Status": "Pending",
            "Message": "Nomination submitted successfully"
        }

@app.get("/api/nominations/pending", response_model=List[Nomination])
async def get_pending_nominations(current_user: dict = Depends(get_current_user)):
    """Get nominations pending approval for current user (as manager)"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT n.NominationId, n.NominatorId, n.BeneficiaryId, n.ApproverId,
                   n.DollarAmount, n.NominationDescription, n.NominationDate,
                   n.ApprovedDate, n.PayedDate
            FROM Nominations n
            WHERE n.ApproverId = ? AND n.ApprovedDate IS NULL
            ORDER BY n.NominationDate DESC
        """, (current_user["UserId"],))
        
        nominations = []
        for row in cursor.fetchall():
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

@app.post("/api/nominations/approve")
async def approve_nomination(
    approval: NominationApproval,
    current_user: dict = Depends(get_current_user)
):
    """Approve or reject a nomination"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Verify user is the approver
        cursor.execute(
            "SELECT ApproverId FROM Nominations WHERE NominationId = ?",
            (approval.NominationId,)
        )
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Nomination not found"
            )
        
        if row[0] != current_user["UserId"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to approve this nomination"
            )
        
        if approval.Approved:
            # Approve nomination
            cursor.execute("""
                UPDATE Nominations 
                SET ApprovedDate = GETDATE()
                WHERE NominationId = ?
            """, (approval.NominationId,))
            
            conn.commit()
            
            # Generate payroll extract file
            await generate_payroll_extract(approval.NominationId)
            
            return {"Status": "Approved", "Message": "Nomination approved successfully"}
        else:
            # Reject nomination (you may want to add a Rejected status column)
            cursor.execute("""
                DELETE FROM Nominations WHERE NominationId = ?
            """, (approval.NominationId,))
            
            conn.commit()
            
            return {"Status": "Rejected", "Message": "Nomination rejected"}

@app.get("/api/nominations/history", response_model=List[Nomination])
async def get_nomination_history(current_user: dict = Depends(get_current_user)):
    """Get nomination history for current user"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT n.NominationId, n.NominatorId, n.BeneficiaryId, n.ApproverId,
                   n.DollarAmount, n.NominationDescription, n.NominationDate,
                   n.ApprovedDate, n.PayedDate
            FROM Nominations n
            WHERE n.NominatorId = ? OR n.BeneficiaryId = ?
            ORDER BY n.NominationDate DESC
        """, (current_user["UserId"], current_user["UserId"]))
        
        nominations = []
        for row in cursor.fetchall():
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

# ============================================================================
# PAYROLL EXTRACT GENERATION
# ============================================================================

async def generate_payroll_extract(nomination_id: int):
    """Generate payroll extract file for approved nomination"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT n.BeneficiaryId, n.DollarAmount, n.NominationDate,
                   u.FirstName, u.LastName
            FROM Nominations n
            JOIN Users u ON n.BeneficiaryId = u.UserId
            WHERE n.NominationId = ?
        """, (nomination_id,))
        
        row = cursor.fetchone()
        if row:
            # Generate CSV file for payroll system
            extract_filename = f"payroll_extract_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            
            with open(extract_filename, 'w') as f:
                f.write("EmployeeId,FirstName,LastName,AwardAmount,Date\n")
                f.write(f"{row[0]},{row[3]},{row[4]},{row[1]},{row[2]}\n")
            
            # Update PayedDate
            cursor.execute("""
                UPDATE Nominations 
                SET PayedDate = GETDATE()
                WHERE NominationId = ?
            """, (nomination_id,))
            
            conn.commit()
            
            print(f"Payroll extract generated: {extract_filename}")
            
            # In production, upload to Azure Blob Storage or SFTP to payroll system

# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)