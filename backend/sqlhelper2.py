"""
sqlhelper2.py – SQLAlchemy-based database helper
=================================================
Drop-in replacement for sqlhelper.py (pyodbc/ODBC).

Authentication modes (controlled by env vars, same as sqlhelper.py):
  • Managed Identity  – USE_MANAGED_IDENTITY=true   (Azure App Service / Functions)
  • SQL Authentication – SQL_USER + SQL_PASSWORD set  (development)
  • Azure AD Interactive – fallback                   (development / interactive)

Code-First schema
-----------------
ORM models (UserORM, NominationORM, ImpersonationAuditLogORM, FraudScoreORM)
are defined with SQLAlchemy Declarative Base.  Call ``create_all_tables()``
once at startup (or use Alembic migrations) to create/update the schema.

Public API is identical to sqlhelper.py so callers need zero changes.
"""

import os
import struct
from contextlib import contextmanager
from typing import Optional, Tuple, List
from urllib.parse import quote_plus

import logging

from sqlalchemy import (
    create_engine, text,
    Column, Integer, String, Float, DateTime, ForeignKey,
)
from sqlalchemy.orm import (
    sessionmaker, DeclarativeBase, Session, relationship,
)
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment / configuration  (mirrors sqlhelper.py)
# ---------------------------------------------------------------------------
DB_SERVER   = os.getenv("SQL_SERVER")
DB_NAME     = os.getenv("SQL_DATABASE")
DB_DRIVER   = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
DB_USERNAME = os.getenv("SQL_USER")
DB_PASSWORD = os.getenv("SQL_PASSWORD")

USE_MANAGED_IDENTITY = os.getenv("USE_MANAGED_IDENTITY", "false").lower() == "true"


# ===========================================================================
# ORM Models  (Code-First schema definition)
# ===========================================================================

class Base(DeclarativeBase):
    pass


class UserORM(Base):
    """Maps to the [Users] table."""
    __tablename__ = "Users"

    UserId            = Column(Integer, primary_key=True)
    userPrincipalName = Column(String(256), nullable=False, unique=True)
    userEmail         = Column(String(256), nullable=True)
    FirstName         = Column(String(128), nullable=True)
    LastName          = Column(String(128), nullable=True)
    Title             = Column(String(256), nullable=True)
    ManagerId         = Column(Integer, ForeignKey("Users.UserId"), nullable=True)

    # Self-referential manager relationship
    manager = relationship("UserORM", remote_side=[UserId])

    # Nomination relationships
    nominations_sent     = relationship(
        "NominationORM", foreign_keys="NominationORM.NominatorId",
        back_populates="nominator",
    )
    nominations_received = relationship(
        "NominationORM", foreign_keys="NominationORM.BeneficiaryId",
        back_populates="beneficiary",
    )
    nominations_approved = relationship(
        "NominationORM", foreign_keys="NominationORM.ApproverId",
        back_populates="approver",
    )


class NominationORM(Base):
    """Maps to the [Nominations] table."""
    __tablename__ = "Nominations"

    NominationId          = Column(Integer, primary_key=True, autoincrement=True)
    NominatorId           = Column(Integer, ForeignKey("Users.UserId"), nullable=False)
    BeneficiaryId         = Column(Integer, ForeignKey("Users.UserId"), nullable=False)
    ApproverId            = Column(Integer, ForeignKey("Users.UserId"), nullable=False)
    DollarAmount          = Column(Integer, nullable=False)
    NominationDescription = Column(String(500), nullable=True)
    NominationDate        = Column(DateTime, server_default=text("GETDATE()"))
    Status                = Column(String(50), default="Pending")
    ApprovedDate          = Column(DateTime, nullable=True)
    PayedDate             = Column(DateTime, nullable=True)

    nominator   = relationship("UserORM", foreign_keys=[NominatorId],
                               back_populates="nominations_sent")
    beneficiary = relationship("UserORM", foreign_keys=[BeneficiaryId],
                               back_populates="nominations_received")
    approver    = relationship("UserORM", foreign_keys=[ApproverId],
                               back_populates="nominations_approved")
    fraud_score = relationship("FraudScoreORM", back_populates="nomination",
                               uselist=False)


class ImpersonationAuditLogORM(Base):
    """Maps to the [Impersonation_AuditLog] table."""
    __tablename__ = "Impersonation_AuditLog"

    AuditId         = Column(Integer, primary_key=True, autoincrement=True)
    AdminUPN        = Column(String(256), nullable=False)
    ImpersonatedUPN = Column(String(256), nullable=False)
    Action          = Column(String(128), nullable=False)
    Details         = Column(String(1000), nullable=True)
    IpAddress       = Column(String(64), nullable=True)
    Timestamp       = Column(DateTime, server_default=text("GETDATE()"))


class FraudScoreORM(Base):
    """Maps to the [FraudScores] table."""
    __tablename__ = "FraudScores"

    FraudScoreId = Column(Integer, primary_key=True, autoincrement=True)
    NominationId = Column(Integer, ForeignKey("Nominations.NominationId"),
                          nullable=False)
    FraudScore   = Column(Integer, nullable=False)
    RiskLevel    = Column(String(50), nullable=False)
    FraudFlags   = Column(String(2000), nullable=True)

    nomination = relationship("NominationORM", back_populates="fraud_score")


# ===========================================================================
# Engine factory
# ===========================================================================

def _build_engine():
    """
    Build a SQLAlchemy engine matching the auth mode selected by env vars.
    Mirrors the three-branch logic in sqlhelper.py.
    """
    if USE_MANAGED_IDENTITY:
        # -------------------------------------------------------------------
        # Production: Managed Identity
        # Acquire an AAD token via azure-identity and inject it into each
        # pyodbc connection through the creator callable.
        # NullPool is used because tokens have a limited lifetime – we do not
        # want SQLAlchemy to re-use a connection whose token has expired.
        # -------------------------------------------------------------------
        try:
            from azure.identity import ManagedIdentityCredential
        except ImportError:
            raise RuntimeError(
                "azure-identity is required for Managed Identity auth. "
                "Install it with: pip install azure-identity"
            )

        credential = ManagedIdentityCredential()

        def _creator():
            import pyodbc
            token     = credential.get_token("https://database.windows.net/.default")
            # Encode the bearer token in the format SQL Server expects
            token_bytes  = token.token.encode("UTF-16-LE")
            token_struct = struct.pack(
                f"<I{len(token_bytes)}s", len(token_bytes), token_bytes
            )
            SQL_COPT_SS_ACCESS_TOKEN = 1256
            conn_str = (
                f"Driver={{{DB_DRIVER}}};"
                f"Server={DB_SERVER};"
                f"Database={DB_NAME};"
                f"Encrypt=yes;"
                f"TrustServerCertificate=no;"
            )
            return pyodbc.connect(
                conn_str,
                attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct},
            )

        return create_engine(
            "mssql+pyodbc://",
            creator=_creator,
            poolclass=NullPool,
        )

    elif DB_USERNAME and DB_PASSWORD:
        # -------------------------------------------------------------------
        # Development: SQL Authentication
        # -------------------------------------------------------------------
        odbc_str = (
            f"Driver={{{DB_DRIVER}}};"
            f"Server={DB_SERVER};"
            f"Database={DB_NAME};"
            f"UID={DB_USERNAME};"
            f"PWD={DB_PASSWORD};"
            f"Encrypt=yes;"
            f"TrustServerCertificate=no;"
        )
        return create_engine(
            f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc_str)}",
            pool_pre_ping=True,
        )

    else:
        # -------------------------------------------------------------------
        # Development: Azure AD Interactive (will prompt for login)
        # NullPool avoids reuse of a connection acquired interactively.
        # -------------------------------------------------------------------
        odbc_str = (
            f"Driver={{{DB_DRIVER}}};"
            f"Server={DB_SERVER};"
            f"Database={DB_NAME};"
            f"Authentication=ActiveDirectoryInteractive;"
            f"Encrypt=yes;"
            f"TrustServerCertificate=no;"
        )
        return create_engine(
            f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc_str)}",
            poolclass=NullPool,
        )


engine      = _build_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


# ===========================================================================
# Session helpers  (public – used by FastAPI dependency injection if desired)
# ===========================================================================

def get_db_session() -> Session:
    """Create and return a new SQLAlchemy Session (caller must close it)."""
    return SessionLocal()


@contextmanager
def get_db_context():
    """
    Context manager that yields a Session and handles rollback / close.
    Mirrors the original get_db_context() so all helper functions below work
    without modification.
    """
    session: Session = SessionLocal()
    try:
        yield session
    except Exception as e:
        session.rollback()
        logger.error(f"Error in database context: {e}")
        raise
    finally:
        session.close()


def create_all_tables() -> None:
    """
    Code-First: create all tables declared in the ORM models if they do not
    yet exist in the target database.  Call once at application startup, or
    replace with Alembic autogenerate migrations for full schema diffing.
    """
    Base.metadata.create_all(engine)
    logger.info("All ORM-defined tables ensured in the database.")


# ===========================================================================
# USER QUERIES
# ===========================================================================

def get_user_by_id(user_id: str) -> Optional[Tuple]:
    """
    Get user by Azure AD Object ID.
    Returns: (UserId, userPrincipalName, FirstName, LastName, Title, ManagerId)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT UserId, userPrincipalName, FirstName, LastName, Title, ManagerId
                FROM Users
                WHERE UserId = :user_id
            """),
            {"user_id": user_id},
        ).fetchone()


def get_user_by_upn(upn: str) -> Optional[Tuple]:
    """
    Get user by User Principal Name (email).
    Returns: (UserId, userPrincipalName, FirstName, LastName, Title, ManagerId)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT UserId, userPrincipalName, FirstName, LastName, Title, ManagerId
                FROM Users
                WHERE userPrincipalName = :upn
            """),
            {"upn": upn},
        ).fetchone()


def get_all_users_except(user_id: int) -> List[Tuple]:
    """
    Get all users except the specified one.
    Returns: List of (UserId, userPrincipalName, FirstName, LastName, Title, ManagerId)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT UserId, userPrincipalName, FirstName, LastName, Title, ManagerId
                FROM Users
                WHERE UserId != :user_id
                ORDER BY LastName, FirstName
            """),
            {"user_id": user_id},
        ).fetchall()


def get_user_manager_info(user_id: int) -> Optional[Tuple]:
    """
    Get user's manager information.
    Returns: (ManagerId, FirstName, LastName)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT ManagerId, FirstName, LastName
                FROM Users
                WHERE UserId = :user_id
            """),
            {"user_id": user_id},
        ).fetchone()


def get_user_name_by_id(user_id: int) -> Optional[Tuple]:
    """
    Get user name by ID.
    Returns: (FirstName, LastName, userEmail)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT FirstName, LastName, userEmail
                FROM Users
                WHERE UserId = :user_id
            """),
            {"user_id": user_id},
        ).fetchone()


# ===========================================================================
# NOMINATION QUERIES
# ===========================================================================

def create_nomination(
    nominator_id: int,
    beneficiary_id: int,
    approver_id: int,
    dollar_amount: int,
    description: str,
) -> int:
    """
    Create a new nomination.
    Returns: NominationId

    Uses OUTPUT INSERTED instead of @@IDENTITY so the inserted ID is returned
    safely even when triggers are present on the table.
    """
    with get_db_context() as session:
        result = session.execute(
            text("""
                INSERT INTO Nominations
                    (NominatorId, BeneficiaryId, ApproverId, DollarAmount,
                     NominationDescription, NominationDate, Status, ApprovedDate, PayedDate)
                OUTPUT INSERTED.NominationId
                VALUES (:nominator_id, :beneficiary_id, :approver_id, :dollar_amount,
                        :description, GETDATE(), 'Pending', NULL, NULL)
            """),
            {
                "nominator_id":   nominator_id,
                "beneficiary_id": beneficiary_id,
                "approver_id":    approver_id,
                "dollar_amount":  dollar_amount,
                "description":    description,
            },
        )
        nomination_id = result.fetchone()[0]
        session.commit()
        return nomination_id


def get_pending_nominations_for_approver(approver_id: int) -> List[Tuple]:
    """
    Get all pending nominations for a specific approver.
    Returns: List of (NominationId, NominatorId, BeneficiaryId, ApproverId,
                      DollarAmount, NominationDescription, NominationDate,
                      ApprovedDate, PayedDate, Status)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT n.NominationId, n.NominatorId, n.BeneficiaryId, n.ApproverId,
                       n.DollarAmount, n.NominationDescription, n.NominationDate,
                       n.ApprovedDate, n.PayedDate, n.Status
                FROM Nominations n
                WHERE n.ApproverId = :approver_id AND n.Status = 'Pending'
                ORDER BY n.NominationDate DESC
            """),
            {"approver_id": approver_id},
        ).fetchall()


def get_nomination_approver(nomination_id: int) -> Optional[int]:
    """
    Get the approver ID for a nomination.
    Returns: ApproverId
    """
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT ApproverId
                FROM Nominations
                WHERE NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id},
        ).fetchone()
        return row[0] if row else None


def get_nomination_status(nomination_id: int) -> Optional[str]:
    """Get the current status of a nomination."""
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT Status
                FROM dbo.Nominations
                WHERE NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id},
        ).fetchone()
        return row[0] if row else None


def approve_nomination(nomination_id: int) -> bool:
    """
    Approve a nomination by setting ApprovedDate and Status.
    Returns: True if successful
    """
    with get_db_context() as session:
        result = session.execute(
            text("""
                UPDATE Nominations
                SET ApprovedDate = GETDATE(), Status = 'Approved'
                WHERE NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id},
        )
        session.commit()
        return result.rowcount > 0


def reject_nomination(nomination_id: int) -> bool:
    """
    Reject a nomination by setting Status to 'Rejected'.
    Returns: True if successful
    """
    with get_db_context() as session:
        result = session.execute(
            text("""
                UPDATE Nominations
                SET Status = 'Rejected'
                WHERE NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id},
        )
        session.commit()
        return result.rowcount > 0


def get_nomination_details(nomination_id: int) -> Optional[dict]:
    """
    Get nomination details including nominator email, beneficiary name, etc.
    Used for sending email notifications.

    Returns: dict with nomination details, or None if not found.
    """
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT
                    n.NominationId,
                    n.DollarAmount,
                    nominator.userEmail                                AS NominatorEmail,
                    nominator.FirstName + ' ' + nominator.LastName    AS NominatorName,
                    beneficiary.FirstName + ' ' + beneficiary.LastName AS BeneficiaryName,
                    beneficiary.userEmail                              AS BeneficiaryEmail,
                    approver.userEmail                                 AS ApproverEmail,
                    approver.FirstName + ' ' + approver.LastName      AS ApproverName,
                    n.NominationDescription,
                    n.Status
                FROM dbo.Nominations n
                INNER JOIN dbo.Users nominator   ON n.NominatorId   = nominator.UserId
                INNER JOIN dbo.Users beneficiary ON n.BeneficiaryId = beneficiary.UserId
                INNER JOIN dbo.Users approver    ON n.ApproverId    = approver.UserId
                WHERE n.NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id},
        ).fetchone()

    if row:
        return {
            "nomination_id":     int(row[0]),
            "dollar_amount":     float(row[1]),
            "nominator_email":   row[2],
            "nominator_name":    row[3],
            "beneficiary_name":  row[4],
            "beneficiary_email": row[5],
            "approver_email":    row[6],
            "approver_name":     row[7],
            "description":       row[8],
            "status":            row[9],
        }
    return None


def get_nomination_history(user_id: int) -> List[Tuple]:
    """
    Get nomination history for a user (as nominator).
    Returns: List of (NominationId, NominatorId, BeneficiaryId, ApproverId,
                      DollarAmount, NominationDescription, NominationDate,
                      ApprovedDate, PayedDate, Status)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT n.NominationId, n.NominatorId, n.BeneficiaryId, n.ApproverId,
                       n.DollarAmount, n.NominationDescription, n.NominationDate,
                       n.ApprovedDate, n.PayedDate, n.Status
                FROM Nominations n
                WHERE n.NominatorId = :user_id
                ORDER BY n.NominationDate DESC
            """),
            {"user_id": user_id},
        ).fetchall()


def get_nomination_for_payroll(nomination_id: int) -> Optional[Tuple]:
    """
    Get nomination details for payroll extract.
    Returns: (BeneficiaryId, DollarAmount, NominationDate, FirstName, LastName)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT n.BeneficiaryId, n.DollarAmount, n.NominationDate,
                       u.FirstName, u.LastName
                FROM Nominations n
                JOIN Users u ON n.BeneficiaryId = u.UserId
                WHERE n.NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id},
        ).fetchone()


def mark_nomination_as_paid(nomination_id: int) -> bool:
    """
    Mark a nomination as paid by setting PayedDate and Status.
    Returns: True if successful
    """
    with get_db_context() as session:
        result = session.execute(
            text("""
                UPDATE Nominations
                SET PayedDate = GETDATE(), Status = 'Paid'
                WHERE NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id},
        )
        session.commit()
        return result.rowcount > 0


# ===========================================================================
# IMPERSONATION & AUDIT LOG QUERIES
# ===========================================================================

def log_impersonation(
    admin_upn: str,
    impersonated_upn: str,
    action: str,
    details: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> bool:
    """
    Log an impersonation action to the audit table.
    Returns: True if successful
    """
    with get_db_context() as session:
        result = session.execute(
            text("""
                INSERT INTO Impersonation_AuditLog
                    (AdminUPN, ImpersonatedUPN, Action, Details, IpAddress, Timestamp)
                VALUES (:admin_upn, :impersonated_upn, :action, :details, :ip_address, GETDATE())
            """),
            {
                "admin_upn":        admin_upn,
                "impersonated_upn": impersonated_upn,
                "action":           action,
                "details":          details,
                "ip_address":       ip_address,
            },
        )
        session.commit()
        return result.rowcount > 0


def get_audit_logs(limit: int = 100) -> List[Tuple]:
    """
    Get recent audit logs.
    Returns: List of (Timestamp, AdminUPN, ImpersonatedUPN, Action, Details, IpAddress)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT TOP (:limit) Timestamp, AdminUPN, ImpersonatedUPN,
                       Action, Details, IpAddress
                FROM Impersonation_AuditLog
                ORDER BY Timestamp DESC
            """),
            {"limit": limit},
        ).fetchall()


# ===========================================================================
# FRAUD DETECTION QUERIES
# ===========================================================================

def get_nominator_history(nominator_id: int) -> List[Tuple]:
    """Get all previous nominations by this nominator."""
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT NominationId, BeneficiaryId, DollarAmount, NominationDate
                FROM Nominations
                WHERE NominatorId = :nominator_id
                ORDER BY NominationDate DESC
            """),
            {"nominator_id": nominator_id},
        ).fetchall()


def get_beneficiary_history(beneficiary_id: int) -> List[Tuple]:
    """Get all previous nominations received by this beneficiary."""
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT NominationId, NominatorId, DollarAmount, NominationDate
                FROM Nominations
                WHERE BeneficiaryId = :beneficiary_id
                ORDER BY NominationDate DESC
            """),
            {"beneficiary_id": beneficiary_id},
        ).fetchall()


def get_approver_history(approver_id: int) -> List[Tuple]:
    """Get all previous nominations approved by this approver."""
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT NominationId,
                       DATEDIFF(HOUR, NominationDate, ApprovedDate) AS HoursToApproval
                FROM Nominations
                WHERE ApproverId = :approver_id
                  AND ApprovedDate IS NOT NULL
                ORDER BY NominationDate DESC
            """),
            {"approver_id": approver_id},
        ).fetchall()


def check_reciprocal_nomination(nominator_id: int, beneficiary_id: int) -> bool:
    """Check if there's a reciprocal nomination (B nominated A when A nominates B)."""
    with get_db_context() as session:
        result = session.execute(
            text("""
                SELECT COUNT(*) AS Count
                FROM Nominations
                WHERE NominatorId = :beneficiary_id AND BeneficiaryId = :nominator_id
            """),
            {"beneficiary_id": beneficiary_id, "nominator_id": nominator_id},
        ).fetchone()
        return result[0] > 0 if result else False


def get_pair_nomination_count(nominator_id: int, beneficiary_id: int) -> int:
    """Get count of nominations from this nominator to this beneficiary."""
    with get_db_context() as session:
        result = session.execute(
            text("""
                SELECT COUNT(*) AS Count
                FROM Nominations
                WHERE NominatorId = :nominator_id AND BeneficiaryId = :beneficiary_id
            """),
            {"nominator_id": nominator_id, "beneficiary_id": beneficiary_id},
        ).fetchone()
        return result[0] if result else 0


def get_overall_amount_stats() -> Tuple[float, float]:
    """Get mean and standard deviation of all nomination amounts."""
    with get_db_context() as session:
        result = session.execute(
            text("""
                SELECT AVG(CAST(DollarAmount AS FLOAT)) AS MeanAmount,
                       STDEV(CAST(DollarAmount AS FLOAT)) AS StdAmount
                FROM Nominations
            """)
        ).fetchone()
        return result if result else (0.0, 0.0)


def save_fraud_assessment(
    nomination_id: int,
    fraud_score: int,
    risk_level: str,
    warning_flags: str,
) -> bool:
    """Save fraud assessment to database."""
    with get_db_context() as session:
        result = session.execute(
            text("""
                INSERT INTO FraudScores (NominationId, FraudScore, RiskLevel, FraudFlags)
                VALUES (:nomination_id, :fraud_score, :risk_level, :warning_flags)
            """),
            {
                "nomination_id": nomination_id,
                "fraud_score":   fraud_score,
                "risk_level":    risk_level,
                "warning_flags": warning_flags,
            },
        )
        session.commit()
        return result.rowcount > 0


# ===========================================================================
# ANALYTICS QUERIES
# ===========================================================================

def get_analytics_overview() -> dict:
    """Get high-level analytics metrics."""
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT
                    COUNT(*)                                                AS totalNominations,
                    SUM(DollarAmount)                                       AS totalAmount,
                    SUM(CASE WHEN Status = 'Approved' THEN 1 ELSE 0 END)   AS approvedCount,
                    SUM(CASE WHEN Status = 'Pending'  THEN 1 ELSE 0 END)   AS pendingCount,
                    AVG(CAST(DollarAmount AS FLOAT))                        AS avgAmount,
                    SUM(CASE WHEN Status = 'Rejected' THEN 1 ELSE 0 END)   AS rejectedCount
                FROM Nominations
            """)
        ).fetchone()
        if row:
            total = row[0] or 0
            return {
                "totalNominations": total,
                "totalAmount":      row[1] or 0,
                "approvedCount":    row[2] or 0,
                "pendingCount":     row[3] or 0,
                "avgAmount":        row[4] or 0,
                "rejectedCount":    row[5] or 0,
                "rejectionRate":    (row[5] or 0) / total if total > 0 else 0,
            }
        return {}


def get_spending_trends(days: int = 90) -> List[Tuple]:
    """
    Get spending trends over last N days.

    Note: the original used an f-string to embed ``days`` directly into SQL.
    Here we use a bound parameter (:neg_days) to prevent any risk of injection.
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT
                    CAST(NominationDate AS DATE) AS PeriodDate,
                    COUNT(*)                     AS NominationCount,
                    SUM(DollarAmount)            AS TotalAmount
                FROM Nominations
                WHERE NominationDate >= DATEADD(DAY, :neg_days, CAST(GETDATE() AS DATE))
                GROUP BY CAST(NominationDate AS DATE)
                ORDER BY PeriodDate DESC
            """),
            {"neg_days": -abs(days)},
        ).fetchall()


def get_department_spending() -> List[Tuple]:
    """Get spending by department."""
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT
                    u.Title                            AS Department,
                    COUNT(n.NominationId)              AS NominationCount,
                    SUM(n.DollarAmount)                AS TotalSpent,
                    AVG(CAST(n.DollarAmount AS FLOAT)) AS AvgAmount
                FROM Nominations n
                JOIN Users u ON n.BeneficiaryId = u.UserId
                WHERE n.Status IN ('Approved', 'Paid')
                GROUP BY u.Title
                ORDER BY TotalSpent DESC
            """)
        ).fetchall()


def get_top_recipients(limit: int = 10) -> List[Tuple]:
    """Get top recipients by count."""
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT TOP (:limit)
                    u.UserId,
                    u.FirstName,
                    u.LastName,
                    COUNT(n.NominationId) AS NominationCount,
                    SUM(n.DollarAmount)   AS TotalAmount
                FROM Nominations n
                JOIN Users u ON n.BeneficiaryId = u.UserId
                WHERE n.Status IN ('Approved', 'Paid')
                GROUP BY u.UserId, u.FirstName, u.LastName
                ORDER BY NominationCount DESC
            """),
            {"limit": limit},
        ).fetchall()


def get_top_nominators(limit: int = 10) -> List[Tuple]:
    """Get top nominators by count."""
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT TOP (:limit)
                    u.UserId,
                    u.FirstName,
                    u.LastName,
                    COUNT(n.NominationId) AS NominationCount,
                    SUM(n.DollarAmount)   AS TotalAmount
                FROM Nominations n
                JOIN Users u ON n.NominatorId = u.UserId
                WHERE n.Status IN ('Approved', 'Paid')
                GROUP BY u.UserId, u.FirstName, u.LastName
                ORDER BY NominationCount DESC
            """),
            {"limit": limit},
        ).fetchall()


def get_top_recipients_by_department(department: str, limit: int = 5) -> List[Tuple]:
    """Get top recipients within a specific department."""
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT TOP (:limit)
                    u.UserId,
                    u.FirstName,
                    u.LastName,
                    COUNT(n.NominationId) AS NominationCount,
                    SUM(n.DollarAmount)   AS TotalAmount
                FROM Nominations n
                JOIN Users u ON n.BeneficiaryId = u.UserId
                WHERE n.Status IN ('Approved', 'Paid')
                  AND u.Title = :department
                GROUP BY u.UserId, u.FirstName, u.LastName
                ORDER BY NominationCount DESC
            """),
            {"limit": limit, "department": department},
        ).fetchall()


def get_top_nominators_by_department(department: str, limit: int = 5) -> List[Tuple]:
    """Get top nominators within a specific department."""
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT TOP (:limit)
                    u.UserId,
                    u.FirstName,
                    u.LastName,
                    COUNT(n.NominationId) AS NominationCount,
                    SUM(n.DollarAmount)   AS TotalAmount
                FROM Nominations n
                JOIN Users u ON n.NominatorId = u.UserId
                WHERE n.Status IN ('Approved', 'Paid')
                  AND u.Title = :department
                GROUP BY u.UserId, u.FirstName, u.LastName
                ORDER BY NominationCount DESC
            """),
            {"limit": limit, "department": department},
        ).fetchall()


def get_fraud_alerts(limit: int = 20) -> List[Tuple]:
    """Get recent fraud alerts."""
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT TOP (:limit)
                    fs.NominationId,
                    fs.FraudScore,
                    fs.RiskLevel,
                    fs.FraudFlags,
                    nominator.FirstName   AS NominatorFirstName,
                    nominator.LastName    AS NominatorLastName,
                    beneficiary.FirstName AS BeneficiaryFirstName,
                    beneficiary.LastName  AS BeneficiaryLastName,
                    n.DollarAmount,
                    n.NominationDate
                FROM FraudScores fs
                JOIN Nominations n    ON fs.NominationId = n.NominationId
                JOIN Users nominator   ON n.NominatorId   = nominator.UserId
                JOIN Users beneficiary ON n.BeneficiaryId = beneficiary.UserId
                WHERE fs.RiskLevel IN ('High', 'Medium')
                ORDER BY n.NominationDate DESC
            """),
            {"limit": limit},
        ).fetchall()


def get_approval_metrics() -> dict:
    """Get approval/rejection metrics."""
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT
                    COUNT(*) AS TotalNominations,
                    SUM(CASE WHEN Status = 'Approved' THEN 1 ELSE 0 END) AS ApprovedCount,
                    SUM(CASE WHEN Status = 'Rejected' THEN 1 ELSE 0 END) AS RejectedCount,
                    AVG(CAST(DATEDIFF(DAY, NominationDate, ApprovedDate) AS FLOAT)) AS AvgDaysToApproval
                FROM Nominations
                WHERE ApprovedDate IS NOT NULL
            """)
        ).fetchone()
        if row:
            total    = row[0] or 0
            approved = row[1] or 0
            return {
                "totalNominations":  total,
                "approvedCount":     approved,
                "rejectedCount":     row[2] or 0,
                "avgDaysToApproval": row[3] or 0,
                "approvalRate":      approved / total if total > 0 else 0,
            }
        return {}


def get_diversity_metrics() -> dict:
    """Calculate diversity metrics for award distribution."""
    with get_db_context() as session:
        # Single session for all three queries – avoids three round-trips
        summary_row = session.execute(
            text("""
                SELECT
                    COUNT(DISTINCT BeneficiaryId) AS UniqueRecipients,
                    COUNT(*)                      AS TotalNominations
                FROM Nominations
                WHERE Status IN ('Approved', 'Paid')
            """)
        ).fetchone()
        unique_recipients = summary_row[0] or 1
        total_nominations = summary_row[1] or 1

        # Individual recipient counts for Gini coefficient
        counts = [
            row[0]
            for row in session.execute(
                text("""
                    SELECT COUNT(*) AS RecipientCount
                    FROM Nominations
                    WHERE Status IN ('Approved', 'Paid')
                    GROUP BY BeneficiaryId
                """)
            ).fetchall()
        ]

        # Gini coefficient (identical logic to original)
        if counts:
            counts.sort()
            n      = len(counts)
            cumsum = sum((i + 1) * c for i, c in enumerate(counts))
            gini   = (2 * cumsum) / (n * sum(counts)) - (n + 1) / n
        else:
            gini = 0

        top_row = session.execute(
            text("""
                SELECT TOP (1) COUNT(*) AS TopRecipientCount
                FROM Nominations
                WHERE Status IN ('Approved', 'Paid')
                GROUP BY BeneficiaryId
                ORDER BY COUNT(*) DESC
            """)
        ).fetchone()
        top_recipient_count   = top_row[0] if top_row else 0
        top_recipient_percent = (
            top_recipient_count / total_nominations * 100
        ) if total_nominations > 0 else 0

        return {
            "uniqueRecipients":            unique_recipients,
            "totalNominations":            total_nominations,
            "avgNominationsPerRecipient":  total_nominations / unique_recipients
                                           if unique_recipients > 0 else 0,
            "giniCoefficient":             gini,
            "topRecipientPercent":         top_recipient_percent,
        }


# ===========================================================================
# RAW QUERY HELPERS
# ===========================================================================

def run_query(sql: str) -> list:
    """Execute a raw SELECT query and return rows."""
    with get_db_context() as session:
        return session.execute(text(sql)).fetchall()


def run_query_with_columns(sql: str) -> tuple[list, list[str]]:
    """Execute a raw SELECT query and return (rows, column_names)."""
    with get_db_context() as session:
        result  = session.execute(text(sql))
        columns = list(result.keys())
        rows    = result.fetchall()
        return rows, columns
