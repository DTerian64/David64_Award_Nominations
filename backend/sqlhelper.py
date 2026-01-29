import os
import pyodbc
from contextlib import contextmanager
from typing import Optional, Tuple, List

# Database Configuration
DB_SERVER = os.getenv("SQL_SERVER")
DB_NAME = os.getenv("SQL_DATABASE")
DB_DRIVER = os.getenv("DB_DRIVER", "{ODBC Driver 18 for SQL Server}")
DB_USERNAME= os.getenv("SQL_USER")
DB_PASSWORD = os.getenv("SQL_PASSWORD")

USE_MANAGED_IDENTITY = os.getenv("USE_MANAGED_IDENTITY", "false").lower() == "true"
if USE_MANAGED_IDENTITY:
    # Production: Use Managed Identity (for Azure App Service, Azure Functions, etc.)
    CONNECTION_STRING = (
    f"Driver={DB_DRIVER};"
    f"Server={DB_SERVER};"
    f"Database={DB_NAME};"
    f"Authentication=ActiveDirectoryMsi;"
    f"Encrypt=yes;"
    f"TrustServerCertificate=no;"
    )
elif DB_USERNAME and DB_PASSWORD:
    # Development: Use SQL Authentication
    CONNECTION_STRING = (
        f"Driver={DB_DRIVER};"
        f"Server={DB_SERVER};"
        f"Database={DB_NAME};"
        f"UID={DB_USERNAME};"
        f"PWD={DB_PASSWORD};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no;"
    )
else:
    # Development: Use Azure AD Interactive (will prompt for login)
    CONNECTION_STRING = (
        f"Driver={DB_DRIVER};"
        f"Server={DB_SERVER};"
        f"Database={DB_NAME};"
        f"Authentication=ActiveDirectoryInteractive;"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no;"
    )    


def get_db_connection():
    """Create a new database connection"""
    try:
        print(f"Attempting to connect to database with connection string: {CONNECTION_STRING}")
        return pyodbc.connect(CONNECTION_STRING)
    except Exception as e:
        print(f"Error connecting to database: {e}")
        raise


@contextmanager
def get_db_context():
    """Database connection context manager"""
    conn = get_db_connection()
    try:
        print("Database connection established.")
        yield conn
    finally:
        conn.close()


# ============================================================================
# USER QUERIES
# ============================================================================

def get_user_by_id(user_id: str) -> Optional[Tuple]:
    """
    Get user by Azure AD Object ID
    Returns: (UserId, userPrincipalName, FirstName, LastName, Title, ManagerId)
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT UserId, userPrincipalName, FirstName, LastName, Title, ManagerId
            FROM Users
            WHERE UserId = ?
        """, (user_id,))
        return cursor.fetchone()


def get_user_by_upn(upn: str) -> Optional[Tuple]:
    """
    Get user by User Principal Name (email)
    Returns: (UserId, userPrincipalName, FirstName, LastName, Title, ManagerId)
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT UserId, userPrincipalName, FirstName, LastName, Title, ManagerId
            FROM Users
            WHERE userPrincipalName = ?
        """, (upn,))
        return cursor.fetchone()


def get_all_users_except(user_id: int) -> List[Tuple]:
    """
    Get all users except the specified one
    Returns: List of (UserId, userPrincipalName, FirstName, LastName, Title, ManagerId)
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT UserId, userPrincipalName, FirstName, LastName, Title, ManagerId 
            FROM Users 
            WHERE UserId != ?
            ORDER BY LastName, FirstName
        """, (user_id,))
        return cursor.fetchall()


def get_user_manager_info(user_id: int) -> Optional[Tuple]:
    """
    Get user's manager information
    Returns: (ManagerId, BeneficiaryFirstName, BeneficiaryLastName)
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ManagerId, FirstName, LastName 
            FROM Users 
            WHERE UserId = ?
        """, (user_id,))
        return cursor.fetchone()


def get_user_name_by_id(user_id: int) -> Optional[Tuple]:
    """
    Get user name by ID
    Returns: (FirstName, LastName)
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT FirstName, LastName 
            FROM Users 
            WHERE UserId = ?
        """, (user_id,))
        return cursor.fetchone()


# ============================================================================
# NOMINATION QUERIES
# ============================================================================

def create_nomination(nominator_id: int, beneficiary_id: int, approver_id: int, 
                     dollar_amount: int, description: str) -> int:
    """
    Create a new nomination
    Returns: NominationId
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO Nominations 
            (NominatorId, BeneficiaryId, ApproverId, DollarAmount, 
             NominationDescription, NominationDate, Status, ApprovedDate, PayedDate)
            VALUES (?, ?, ?, ?, ?, GETDATE(), 'Pending', NULL, NULL)
        """, (nominator_id, beneficiary_id, approver_id, dollar_amount, description))
        conn.commit()
        
        cursor.execute("SELECT @@IDENTITY")
        return cursor.fetchone()[0]


def get_pending_nominations_for_approver(approver_id: int) -> List[Tuple]:
    """
    Get all pending nominations for a specific approver
    Returns: List of (NominationId, NominatorId, BeneficiaryId, ApproverId,
                     DollarAmount, NominationDescription, NominationDate,
                     ApprovedDate, PayedDate, Status)
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT n.NominationId, n.NominatorId, n.BeneficiaryId, n.ApproverId,
                   n.DollarAmount, n.NominationDescription, n.NominationDate,
                   n.ApprovedDate, n.PayedDate, n.Status
            FROM Nominations n
            WHERE n.ApproverId = ? AND n.Status = 'Pending'
            ORDER BY n.NominationDate DESC
        """, (approver_id,))
        return cursor.fetchall()


def get_nomination_approver(nomination_id: int) -> Optional[int]:
    """
    Get the approver ID for a nomination
    Returns: ApproverId
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ApproverId 
            FROM Nominations 
            WHERE NominationId = ?
        """, (nomination_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def approve_nomination(nomination_id: int) -> bool:
    """
    Approve a nomination by setting ApprovedDate and Status
    Returns: True if successful
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE Nominations 
            SET ApprovedDate = GETDATE(), Status = 'Approved'
            WHERE NominationId = ?
        """, (nomination_id,))
        conn.commit()
        return cursor.rowcount > 0


def reject_nomination(nomination_id: int) -> bool:
    """
    Reject a nomination by setting Status to 'Rejected'
    Returns: True if successful
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE Nominations 
            SET Status = 'Rejected'
            WHERE NominationId = ?
        """, (nomination_id,))
        conn.commit()
        return cursor.rowcount > 0


def get_nomination_history(user_id: int) -> List[Tuple]:
    """
    Get nomination history for a user (as nominator or beneficiary)
    Returns: List of (NominationId, NominatorId, BeneficiaryId, ApproverId,
                     DollarAmount, NominationDescription, NominationDate,
                     ApprovedDate, PayedDate, Status)
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT n.NominationId, n.NominatorId, n.BeneficiaryId, n.ApproverId,
                   n.DollarAmount, n.NominationDescription, n.NominationDate,
                   n.ApprovedDate, n.PayedDate, n.Status
            FROM Nominations n
            WHERE n.NominatorId = ? OR n.BeneficiaryId = ?
            ORDER BY n.NominationDate DESC
        """, (user_id, user_id))
        return cursor.fetchall()


def get_nomination_for_payroll(nomination_id: int) -> Optional[Tuple]:
    """
    Get nomination details for payroll extract
    Returns: (BeneficiaryId, DollarAmount, NominationDate, FirstName, LastName)
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT n.BeneficiaryId, n.DollarAmount, n.NominationDate,
                   u.FirstName, u.LastName
            FROM Nominations n
            JOIN Users u ON n.BeneficiaryId = u.UserId
            WHERE n.NominationId = ?
        """, (nomination_id,))
        return cursor.fetchone()


def mark_nomination_as_paid(nomination_id: int) -> bool:
    """
    Mark a nomination as paid by setting PayedDate and Status
    Returns: True if successful
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE Nominations 
            SET PayedDate = GETDATE(), Status = 'Paid'
            WHERE NominationId = ?
        """, (nomination_id,))
        conn.commit()
        return cursor.rowcount > 0
    
# ============================================================================
# IMPERSONATION & AUDIT LOG QUERIES
# ============================================================================

def log_impersonation(admin_upn: str, impersonated_upn: str, action: str, 
                     details: Optional[str] = None, ip_address: Optional[str] = None) -> int:
    """
    Log an impersonation action to the audit table
    Returns: AuditId
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO Impersonation_AuditLog 
            (AdminUPN, ImpersonatedUPN, Action, Details, IpAddress, Timestamp)
            VALUES (?, ?, ?, ?, ?, GETDATE())
        """, (admin_upn, impersonated_upn, action, details, ip_address))
        conn.commit()
        
        cursor.execute("SELECT @@IDENTITY")
        return cursor.fetchone()[0]


def get_audit_logs(limit: int = 100) -> List[Tuple]:
    """
    Get recent audit logs
    Returns: List of (AuditId, Timestamp, AdminUPN, ImpersonatedUPN, Action, Details, IpAddress)
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TOP (?) AuditId, Timestamp, AdminUPN, ImpersonatedUPN, 
                   Action, Details, IpAddress
            FROM Impersonation_AuditLog
            ORDER BY Timestamp DESC
        """, (limit,))
        return cursor.fetchall()
