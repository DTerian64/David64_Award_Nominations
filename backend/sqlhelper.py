import os
import pyodbc
from contextlib import contextmanager
from typing import Optional, Tuple, List

import logging
logger = logging.getLogger(__name__) 

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
        return pyodbc.connect(CONNECTION_STRING) 
    except Exception as e:
        logger.error(f"Error connecting to database: {e}")
        raise


@contextmanager
def get_db_context():
    """Database connection context manager"""
    conn = get_db_connection()
    try:        
        yield conn
    except Exception as e:
        logger.error(f"Error in database context: {e}")
        raise
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
            SELECT FirstName, LastName, userEmail 
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

def get_nomination_status(nomination_id: int) -> Optional[str]:
     with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT Status FROM dbo.Nominations WHERE NominationId = ?""", (nomination_id,))
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

def get_nomination_details(nomination_id: int) -> Optional[dict]:
    """
    Get nomination details including nominator email, beneficiary name, etc.
    Used for sending email notifications.
    
    Args:
        nomination_id: The nomination ID
        
    Returns:
        Optional[dict]: Dictionary containing nomination details or None if not found
    """
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
            n.NominationId,
            n.DollarAmount,
            nominator.userEmail as NominatorEmail,
            nominator.FirstName + ' ' + nominator.LastName as NominatorName,
            beneficiary.FirstName + ' ' + beneficiary.LastName as BeneficiaryName,
            beneficiary.userEmail as BeneficiaryEmail,
            approver.userEmail as ApproverEmail,
            approver.FirstName + ' ' + approver.LastName as ApproverName,
            n.NominationDescription,
            n.Status
            FROM dbo.Nominations n
            INNER JOIN dbo.Users nominator ON n.NominatorId = nominator.UserId
            INNER JOIN dbo.Users beneficiary ON n.BeneficiaryId = beneficiary.UserId
            INNER JOIN dbo.Users approver ON n.ApproverId = approver.UserId
            WHERE n.NominationId = ?
            """, (nomination_id,))
        
        row =  cursor.fetchone()       
    
    if row:
            return {
                'nomination_id': int(row[0]),
                'dollar_amount': float(row[1]),
                'nominator_email': row[2],
                'nominator_name': row[3],
                'beneficiary_name': row[4],
                'beneficiary_email': row[5],
                'approver_email': row[6],
                'approver_name': row[7],
                'description': row[8],
                'status': row[9]
        }
    return None

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
            WHERE n.NominatorId = ?
            ORDER BY n.NominationDate DESC
        """, (user_id,))
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

# ============================================================================
# FRAUD DETECTION QUERIES
# Add these to the end of your sqlhelper.py file
# ============================================================================

def get_nominator_history(nominator_id: int) -> List[Tuple]:
    """Get all previous nominations by this nominator"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT NominationId, BeneficiaryId, DollarAmount, NominationDate
            FROM Nominations
            WHERE NominatorId = ?
            ORDER BY NominationDate DESC
        """, (nominator_id,))
        return cursor.fetchall()


def get_beneficiary_history(beneficiary_id: int) -> List[Tuple]:
    """Get all previous nominations received by this beneficiary"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT NominationId, NominatorId, DollarAmount, NominationDate
            FROM Nominations
            WHERE BeneficiaryId = ?
            ORDER BY NominationDate DESC
        """, (beneficiary_id,))
        return cursor.fetchall()


def get_approver_history(approver_id: int) -> List[Tuple]:
    """Get all previous nominations approved by this approver"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT NominationId, 
                   DATEDIFF(HOUR, NominationDate, ApprovedDate) AS HoursToApproval
            FROM Nominations
            WHERE ApproverId = ?
              AND ApprovedDate IS NOT NULL
            ORDER BY NominationDate DESC
        """, (approver_id,))
        return cursor.fetchall()


def check_reciprocal_nomination(nominator_id: int, beneficiary_id: int) -> bool:
    """Check if there's a reciprocal nomination (B nominated A when A nominates B)"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) as Count
            FROM Nominations
            WHERE NominatorId = ? AND BeneficiaryId = ?
        """, (beneficiary_id, nominator_id))
        result = cursor.fetchone()
        return result[0] > 0 if result else False


def get_pair_nomination_count(nominator_id: int, beneficiary_id: int) -> int:
    """Get count of nominations from this nominator to this beneficiary"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) as Count
            FROM Nominations
            WHERE NominatorId = ? AND BeneficiaryId = ?
        """, (nominator_id, beneficiary_id))
        result = cursor.fetchone()
        return result[0] if result else 0


def get_overall_amount_stats() -> Tuple[float, float]:
    """Get mean and standard deviation of all nomination amounts"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT AVG(CAST(DollarAmount AS FLOAT)) AS MeanAmount,
                   STDEV(CAST(DollarAmount AS FLOAT)) AS StdAmount
            FROM Nominations
        """)
        result = cursor.fetchone()
        return result if result else (0.0, 0.0)


def save_fraud_assessment(nomination_id: int, fraud_score: int, risk_level: str, 
                          warning_flags: str) -> bool:
    """Save fraud assessment to database"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO FraudScores (NominationId, FraudScore, RiskLevel, FraudFlags)
            VALUES (?, ?, ?, ?)
        """, (nomination_id, fraud_score, risk_level, warning_flags))
        conn.commit()
        return cursor.rowcount > 0


# ============================================================================
# ANALYTICS QUERIES
# ============================================================================

def get_analytics_overview() -> dict:
    """Get high-level analytics metrics"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                COUNT(*) as totalNominations,
                SUM(DollarAmount) as totalAmount,
                SUM(CASE WHEN Status = 'Approved' THEN 1 ELSE 0 END) as approvedCount,
                SUM(CASE WHEN Status = 'Pending' THEN 1 ELSE 0 END) as pendingCount,
                AVG(CAST(DollarAmount AS FLOAT)) as avgAmount,
                SUM(CASE WHEN Status = 'Rejected' THEN 1 ELSE 0 END) as rejectedCount
            FROM Nominations
        """)
        row = cursor.fetchone()
        if row:
            total = row[0] or 0
            return {
                'totalNominations': total,
                'totalAmount': row[1] or 0,
                'approvedCount': row[2] or 0,
                'pendingCount': row[3] or 0,
                'avgAmount': row[4] or 0,
                'rejectedCount': row[5] or 0,
                'rejectionRate': (row[5] or 0) / total if total > 0 else 0
            }
        return {}


def get_spending_trends(days: int = 90) -> List[Tuple]:
    """Get spending trends over last N days"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT 
                CAST(NominationDate AS DATE) as PeriodDate,
                COUNT(*) as NominationCount,
                SUM(DollarAmount) as TotalAmount
            FROM Nominations
            WHERE NominationDate >= DATEADD(DAY, -{days}, CAST(GETDATE() AS DATE))
            GROUP BY CAST(NominationDate AS DATE)
            ORDER BY PeriodDate DESC
        """)
        return cursor.fetchall()


def get_department_spending() -> List[Tuple]:
    """Get spending by department"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                u.Title as Department,
                COUNT(n.NominationId) as NominationCount,
                SUM(n.DollarAmount) as TotalSpent,
                AVG(CAST(n.DollarAmount AS FLOAT)) as AvgAmount
            FROM Nominations n
            JOIN Users u ON n.BeneficiaryId = u.UserId
            WHERE n.Status IN ('Approved', 'Paid')
            GROUP BY u.Title
            ORDER BY TotalSpent DESC
        """)
        return cursor.fetchall()


def get_top_recipients(limit: int = 10) -> List[Tuple]:
    """Get top recipients by count"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT TOP {limit}
                u.UserId,
                u.FirstName,
                u.LastName,
                COUNT(n.NominationId) as NominationCount,
                SUM(n.DollarAmount) as TotalAmount
            FROM Nominations n
            JOIN Users u ON n.BeneficiaryId = u.UserId
            WHERE n.Status IN ('Approved', 'Paid')
            GROUP BY u.UserId, u.FirstName, u.LastName
            ORDER BY NominationCount DESC
        """)
        return cursor.fetchall()


def get_top_nominators(limit: int = 10) -> List[Tuple]:
    """Get top nominators by count"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT TOP {limit}
                u.UserId,
                u.FirstName,
                u.LastName,
                COUNT(n.NominationId) as NominationCount,
                SUM(n.DollarAmount) as TotalAmount
            FROM Nominations n
            JOIN Users u ON n.NominatorId = u.UserId
            WHERE n.Status IN ('Approved', 'Paid')
            GROUP BY u.UserId, u.FirstName, u.LastName
            ORDER BY NominationCount DESC
        """)
        return cursor.fetchall()


def get_fraud_alerts(limit: int = 20) -> List[Tuple]:
    """Get recent fraud alerts"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT TOP {limit}
                fs.NominationId,
                fs.FraudScore,
                fs.RiskLevel,
                fs.FraudFlags,
                nominator.FirstName as NominatorFirstName,
                nominator.LastName as NominatorLastName,
                beneficiary.FirstName as BeneficiaryFirstName,
                beneficiary.LastName as BeneficiaryLastName,
                n.DollarAmount,
                n.NominationDate
            FROM FraudScores fs
            JOIN Nominations n ON fs.NominationId = n.NominationId
            JOIN Users nominator ON n.NominatorId = nominator.UserId
            JOIN Users beneficiary ON n.BeneficiaryId = beneficiary.UserId
            WHERE fs.RiskLevel IN ('High', 'Medium')
            ORDER BY n.NominationDate DESC
        """)
        return cursor.fetchall()


def get_approval_metrics() -> dict:
    """Get approval/rejection metrics"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                COUNT(*) as TotalNominations,
                SUM(CASE WHEN Status = 'Approved' THEN 1 ELSE 0 END) as ApprovedCount,
                SUM(CASE WHEN Status = 'Rejected' THEN 1 ELSE 0 END) as RejectedCount,
                AVG(CAST(DATEDIFF(DAY, NominationDate, ApprovedDate) AS FLOAT)) as AvgDaysToApproval
            FROM Nominations
            WHERE ApprovedDate IS NOT NULL
        """)
        row = cursor.fetchone()
        if row:
            total = row[0] or 0
            approved = row[1] or 0
            return {
                'totalNominations': total,
                'approvedCount': approved,
                'rejectedCount': row[2] or 0,
                'avgDaysToApproval': row[3] or 0,
                'approvalRate': approved / total if total > 0 else 0
            }
        return {}


def get_diversity_metrics() -> dict:
    """Calculate diversity metrics for award distribution"""
    with get_db_context() as conn:
        cursor = conn.cursor()
        # Get unique recipients and their award counts
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT BeneficiaryId) as UniqueRecipients,
                COUNT(*) as TotalNominations
            FROM Nominations
            WHERE Status IN ('Approved', 'Paid')
        """)
        row = cursor.fetchone()
        unique_recipients = row[0] or 1
        total_nominations = row[1] or 1
        
        # Get individual recipient counts for Gini calculation
        cursor.execute("""
            SELECT COUNT(*) as RecipientCount
            FROM Nominations
            WHERE Status IN ('Approved', 'Paid')
            GROUP BY BeneficiaryId
        """)
        counts = [r[0] for r in cursor.fetchall()]
        
        # Calculate Gini coefficient
        if counts:
            counts.sort()
            n = len(counts)
            cumsum = 0
            for i, c in enumerate(counts):
                cumsum += (i + 1) * c
            gini = (2 * cumsum) / (n * sum(counts)) - (n + 1) / n
        else:
            gini = 0
        
        # Get top recipient percentage
        cursor.execute("""
            SELECT TOP 1 COUNT(*) as TopRecipientCount
            FROM Nominations
            WHERE Status IN ('Approved', 'Paid')
            GROUP BY BeneficiaryId
            ORDER BY COUNT(*) DESC
        """)
        top_row = cursor.fetchone()
        top_recipient_count = top_row[0] if top_row else 0
        top_recipient_percent = (top_recipient_count / total_nominations * 100) if total_nominations > 0 else 0
        
        return {
            'uniqueRecipients': unique_recipients,
            'totalNominations': total_nominations,
            'avgNominationsPerRecipient': total_nominations / unique_recipients if unique_recipients > 0 else 0,
            'giniCoefficient': gini,
            'topRecipientPercent': top_recipient_percent
        }









