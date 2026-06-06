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

import json
import os
import struct
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import quote_plus

import logging

from sqlalchemy import (
    create_engine, text,
    Column, Integer, String, Float, DateTime, ForeignKey, UniqueConstraint,
    Unicode, Text,
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


class TenantORM(Base):
    """
    Maps to the [Tenants] table.

    One row per customer organisation.  The AzureAdTenantId column stores the
    Azure AD / Entra ID tenant GUID (the ``tid`` claim in every JWT issued by
    that tenant) and is used to resolve which tenant a user belongs to at
    authentication time.
    """
    __tablename__ = "Tenants"

    TenantId             = Column(Integer, primary_key=True, autoincrement=True)
    TenantName           = Column(String(256), nullable=False, unique=True)
    AzureAdTenantId      = Column(String(36),  nullable=False, unique=True)
    Config               = Column(Unicode(None), nullable=True)            # NVARCHAR(MAX) JSON blob, NULL = defaults
    Domain               = Column(String(253),  nullable=True, unique=True)
    fallback_admin_email = Column(String(256),  nullable=True)             # emailed when no HRBP is configured
    Site_URL             = Column(String(256),  nullable=True)             # frontend portal URL for email hyperlinks

    # Reverse relationship — rarely needed directly, but handy for admin queries
    users = relationship("UserORM", back_populates="tenant")


class UserORM(Base):
    """Maps to the [Users] table."""
    __tablename__ = "Users"

    UserId            = Column(Integer, primary_key=True)
    userPrincipalName = Column(String(256), nullable=False)
    userEmail         = Column(String(256), nullable=True)
    FirstName         = Column(Unicode(128), nullable=True)
    LastName          = Column(Unicode(128), nullable=True)
    Title             = Column(Unicode(256), nullable=True)
    ManagerId         = Column(Integer, ForeignKey("Users.UserId"), nullable=True)
    TenantId          = Column(Integer, ForeignKey("Tenants.TenantId"), nullable=False)

    __table_args__ = (
        # UPN is unique per tenant, not globally
        UniqueConstraint("userPrincipalName", "TenantId", name="uq_users_upn_tenant"),
    )

    # Tenant relationship
    tenant = relationship("TenantORM", back_populates="users")

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
    Amount                = Column(Integer, nullable=False)
    Currency              = Column(String(3), nullable=False, default="USD")
    NominationDescription = Column(Unicode(500), nullable=True)
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


class P2PFraudScoreORM(Base):
    """Maps to dbo.P2P_FraudScores — peer-to-peer fraud score at submission time."""
    __tablename__ = "P2P_FraudScores"

    P2PScoreId   = Column(Integer, primary_key=True, autoincrement=True)
    NominationId = Column(Integer, ForeignKey("Nominations.NominationId"), nullable=False)
    FraudScore   = Column(Integer,     nullable=False)
    RiskLevel    = Column(String(20),  nullable=False)
    FraudFlags   = Column(String(500), nullable=True)


class ApprFraudScoreORM(Base):
    """Maps to dbo.Appr_FraudScores — approver-behaviour fraud score (batch job)."""
    __tablename__ = "Appr_FraudScores"

    ApprScoreId  = Column(Integer, primary_key=True, autoincrement=True)
    NominationId = Column(Integer, ForeignKey("Nominations.NominationId"), nullable=False)
    FraudScore   = Column(Integer,     nullable=False)
    RiskLevel    = Column(String(20),  nullable=False)
    FraudFlags   = Column(String(500), nullable=True)


class HRBPFraudFlagORM(Base):
    """Maps to dbo.HRBP_FraudFlags — full inference snapshot for HRBP review queue."""
    __tablename__ = "HRBP_FraudFlags"

    FlagId             = Column(Integer, primary_key=True, autoincrement=True)
    NominationId       = Column(Integer, ForeignKey("Nominations.NominationId"), nullable=False)
    FraudScore         = Column(Integer,     nullable=False)
    FraudProbability   = Column(Float(),     nullable=False)
    RiskLevel          = Column(String(20),  nullable=False)
    WarningFlags       = Column(String(500), nullable=True)
    TopFeaturesJson    = Column(Unicode(None), nullable=True)
    FeatureSummaryJson = Column(Unicode(None), nullable=True)


class NominationCategoryORM(Base):
    """
    Maps to dbo.nomination_categories.

    Per-tenant list of custom award categories (Premium / Enterprise feature).
    When at least one row exists for a tenant the nomination form shows a
    required category dropdown; tenants with no rows see no category field.
    """
    __tablename__ = "nomination_categories"

    id                   = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id            = Column(Integer, ForeignKey("Tenants.TenantId"), nullable=False)
    category_description = Column(Unicode(256), nullable=False)


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


def warmup_database() -> None:
    """Open a SQL connection and run a tiny query to wake Azure SQL Serverless."""
    with get_db_context() as session:
        session.execute(text("SELECT 1")).fetchone()


def create_all_tables() -> None:
    """
    Code-First: create all tables declared in the ORM models if they do not
    yet exist in the target database.  Call once at application startup, or
    replace with Alembic autogenerate migrations for full schema diffing.
    """
    Base.metadata.create_all(engine)
    logger.info("All ORM-defined tables ensured in the database.")


# ===========================================================================
# TENANT QUERIES
# ===========================================================================

def get_tenant_by_aad_id(aad_tenant_id: str) -> Optional[Tuple]:
    """
    Resolve an Azure AD tenant GUID (the ``tid`` JWT claim) to an internal
    Tenant row.

    Returns: (TenantId, TenantName, AzureAdTenantId, Domain) or None if not registered.
    Domain may be None if no domain restriction has been configured for that tenant.
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT TenantId, TenantName, AzureAdTenantId, Domain
                FROM dbo.Tenants
                WHERE AzureAdTenantId = :aad_id
            """),
            {"aad_id": aad_tenant_id},
        ).fetchone()


def get_tenant_branding_by_domain(hostname: str) -> Optional[dict]:
    """
    Return public branding fields for the tenant whose Domain matches *hostname*.

    Called by the public GET /api/tenant/branding endpoint — no auth required.
    The hostname is extracted from the request's Origin header by the caller
    (protocol and port stripped).

    Returns a dict with keys:
        tenant_name   — TenantName from dbo.Tenants
        primary_color — theme.primaryColor from Config JSON (or None)
        company_logo_url      — Company_Logo_URL column (or None)
        tagline       — Tagline column (or None)

    Returns None if no tenant matches the hostname.
    """
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT TenantName, Config, Company_Logo_URL, Tagline
                FROM   dbo.Tenants
                WHERE  Domain = :hostname
            """),
            {"hostname": hostname},
        ).fetchone()
        if not row:
            return None

        import json as _json
        theme: dict = {}
        try:
            cfg = _json.loads(row[1]) if row[1] else {}
            theme = cfg.get("theme", {})
        except Exception:
            pass

        return {
            "tenant_name":          row[0],
            "primary_color":        theme.get("primaryColor"),
            "primary_hover_color":  theme.get("primaryHoverColor"),
            "primary_light_color":  theme.get("primaryLightColor"),
            "primary_text_on_dark": theme.get("primaryTextOnDark"),
            "company_logo_url":     row[2],
            "tagline":              row[3],
        }


def get_site_url_by_user_id(user_id: int) -> Optional[str]:
    """
    Return the Site_URL for the tenant that owns *user_id*, or None if not set.

    Used by the email-action endpoint to build the 'Go to Dashboard' link
    dynamically per tenant instead of hardcoding a URL.
    """
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT t.Site_URL
                FROM   dbo.Tenants t
                JOIN   dbo.Users   u ON u.TenantId = t.TenantId
                WHERE  u.UserId = :user_id
            """),
            {"user_id": user_id},
        ).fetchone()
        return row[0] if row and row[0] else None


def get_tenant_domain(tenant_id: int) -> Optional[str]:
    """
    Return the canonical public hostname for a tenant, or None if not set.

    Used by the /api/tenant/config endpoint to include the domain in the
    response so the frontend can redirect users who land on the wrong domain.
    """
    with get_db_context() as session:
        row = session.execute(
            text("SELECT Domain FROM dbo.Tenants WHERE TenantId = :tid"),
            {"tid": tenant_id},
        ).fetchone()
        return row[0] if row else None


def get_tenant_config(tenant_id: int) -> Optional[str]:
    """
    Return the JSON config string for a tenant, or None if not set.

    The caller is responsible for parsing the JSON.  None means the frontend
    should use application defaults (English, USD, indigo theme).
    """
    with get_db_context() as session:
        row = session.execute(
            text("SELECT Config FROM dbo.Tenants WHERE TenantId = :tid"),
            {"tid": tenant_id},
        ).fetchone()
        return row[0] if row else None


def get_nomination_categories(tenant_id: int) -> List[Tuple]:
    """
    Return all nomination categories for a tenant, ordered by id.

    Returns: List of (id, category_description)
    An empty list means the tenant has no custom categories — the nomination
    form should show no category field at all.
    """
    with get_db_context() as session:
        rows = session.execute(
            text(
                "SELECT id, category_description "
                "FROM dbo.nomination_categories "
                "WHERE tenant_id = :tid "
                "ORDER BY id"
            ),
            {"tid": tenant_id},
        ).fetchall()
        return rows


@dataclass
class DescCheckConfig:
    """
    Per-tenant description quality thresholds, read from
    dbo.Tenants.desc_check_config (NVARCHAR(MAX) JSON).

    Used by the nomination submission endpoint for synchronous API-layer
    validation (word/char count, blocklist phrases) before the nomination
    enters the DB.  The integrity-check pipeline has its own copy of this
    dataclass for the async semantic checks.

    NULL column → all defaults (English, word-count based).
    """
    embed_model:                    str       = "all-MiniLM-L6-v2"
    use_char_count:                 bool      = False
    min_char_count:                 int       = 12
    min_word_count:                 int       = 3
    category_alignment_threshold:   float     = 0.15
    duplicate_similarity_threshold: float     = 0.85
    boilerplate_phrases:            List[str] = field(default_factory=list)


def get_tenant_desc_check_config(tenant_id: int) -> DescCheckConfig:
    """
    Load desc_check_config JSON from dbo.Tenants and return a DescCheckConfig.

    Missing keys fall back to dataclass defaults.  A NULL column or any JSON
    parse error returns a fully-defaulted config — never raises.
    """
    with get_db_context() as session:
        row = session.execute(
            text("SELECT desc_check_config FROM dbo.Tenants WHERE TenantId = :tid"),
            {"tid": tenant_id},
        ).fetchone()

    raw = row[0] if row else None
    if not raw:
        return DescCheckConfig()

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "Invalid JSON in desc_check_config for tenant %d — using defaults",
            tenant_id,
        )
        return DescCheckConfig()

    return DescCheckConfig(
        embed_model=data.get("embed_model", DescCheckConfig.embed_model),
        use_char_count=bool(data.get("use_char_count", DescCheckConfig.use_char_count)),
        min_char_count=int(data.get("min_char_count", DescCheckConfig.min_char_count)),
        min_word_count=int(data.get("min_word_count", DescCheckConfig.min_word_count)),
        category_alignment_threshold=float(
            data.get("category_alignment_threshold",
                     DescCheckConfig.category_alignment_threshold)
        ),
        duplicate_similarity_threshold=float(
            data.get("duplicate_similarity_threshold",
                     DescCheckConfig.duplicate_similarity_threshold)
        ),
        boilerplate_phrases=[
            p.lower() for p in data.get("boilerplate_phrases", [])
        ],
    )


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
    Get user by User Principal Name (email) — tenant-unscoped.
    Kept for backwards compatibility; prefer get_user_by_upn_and_tenant.
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


def get_user_by_upn_and_tenant(
    upn: str,
    tenant_id: int,
    email: Optional[str] = None,
) -> Optional[Tuple]:
    """
    Get user by UPN (or email) scoped to a specific internal TenantId.
    This is the preferred lookup for all authenticated requests.

    Why match on either column
    --------------------------
    For B2B guests, the JWT may carry the guest's `#EXT#` UPN, the home
    identity's email, or both, depending on whether the `upn` optional
    claim is configured on the SPA app registration with the
    "Externally authenticated" extension property.

    The dbo.Users row created at registration stores BOTH:
      - userPrincipalName = the AAD #EXT# UPN (from Graph)
      - userEmail         = the original invited email address

    Matching on either column keeps auth working regardless of which
    claim variant the token carries, and survives the case where the
    Graph UPN fetch at invitation time fell back to email-as-UPN.

    If email is None (older callers that haven't been updated yet), the
    OR clause `userEmail = NULL` is never true and the function behaves
    exactly like the original UPN-only lookup.

    Returns: (UserId, userPrincipalName, FirstName, LastName, Title, ManagerId, TenantId)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT UserId, userPrincipalName, FirstName, LastName, Title, ManagerId, TenantId
                FROM Users
                WHERE (userPrincipalName = :upn OR userEmail = :email)
                  AND TenantId = :tenant_id
            """),
            {"upn": upn, "email": email, "tenant_id": tenant_id},
        ).fetchone()


def get_all_users_except(user_id: int, tenant_id: int) -> List[Tuple]:
    """
    Get all users in the given tenant except the specified one.
    Returns: List of (UserId, userPrincipalName, FirstName, LastName, Title, ManagerId)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT UserId, userPrincipalName, FirstName, LastName, Title, ManagerId
                FROM Users
                WHERE UserId    != :user_id
                  AND TenantId   = :tenant_id
                ORDER BY LastName, FirstName
            """),
            {"user_id": user_id, "tenant_id": tenant_id},
        ).fetchall()


def get_user_manager_info(user_id: int, tenant_id: int) -> Optional[Tuple]:
    """
    Get user's manager information, scoped to the tenant.
    Returns: (ManagerId, FirstName, LastName)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT ManagerId, FirstName, LastName
                FROM Users
                WHERE UserId   = :user_id
                  AND TenantId = :tenant_id
            """),
            {"user_id": user_id, "tenant_id": tenant_id},
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
    amount: int,
    currency: str,
    description: str,
    category_id: Optional[int] = None,
    initial_status: str = 'Submitted',
) -> int:
    """
    Create a new nomination.
    Returns: NominationId

    Uses OUTPUT INSERTED instead of @@IDENTITY so the inserted ID is returned
    safely even when triggers are present on the table.

    category_id is optional — pass None for tenants without custom categories.

    initial_status defaults to 'Submitted' — the nomination.submitted Service Bus
    event triggers the auxiliary service to run fraud detection asynchronously,
    after which the status is moved to 'Pending' or 'PendingHRBPReview'.
    """
    with get_db_context() as session:
        result = session.execute(
            text("""
                INSERT INTO Nominations
                    (NominatorId, BeneficiaryId, ApproverId, Amount, Currency,
                     NominationDescription, NominationDate, Status, ApprovedDate, PayedDate,
                     CategoryId)
                OUTPUT INSERTED.NominationId
                VALUES (:nominator_id, :beneficiary_id, :approver_id, :amount, :currency,
                        :description, GETDATE(), :initial_status, NULL, NULL,
                        :category_id)
            """),
            {
                "nominator_id":   nominator_id,
                "beneficiary_id": beneficiary_id,
                "approver_id":    approver_id,
                "amount":         amount,
                "currency":       currency,
                "description":    description,
                "category_id":    category_id,
                "initial_status": initial_status,
            },
        )
        nomination_id = result.fetchone()[0]
        session.commit()
        return nomination_id


def get_pending_nominations_for_approver(approver_id: int, tenant_id: int) -> List[Tuple]:
    """
    Get all pending nominations for a specific approver, scoped to the tenant.
    Returns: List of (NominationId, NominatorId, BeneficiaryId, ApproverId,
                      Amount, Currency, NominationDescription, NominationDate,
                      ApprovedDate, PayedDate, Status, CategoryDescription)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT n.NominationId, n.NominatorId, n.BeneficiaryId, n.ApproverId,
                       n.Amount, n.Currency, n.NominationDescription, n.NominationDate,
                       n.ApprovedDate, n.PayedDate, n.Status,
                       nc.category_description AS CategoryDescription
                FROM Nominations n
                JOIN Users u ON n.NominatorId = u.UserId
                LEFT JOIN dbo.nomination_categories nc ON nc.id = n.CategoryId
                WHERE n.ApproverId = :approver_id
                  AND n.Status     = 'Pending'
                  AND u.TenantId   = :tenant_id
                ORDER BY n.NominationDate DESC
            """),
            {"approver_id": approver_id, "tenant_id": tenant_id},
        ).fetchall()


def get_nomination_approver(nomination_id: int, tenant_id: Optional[int] = None) -> Optional[int]:
    """
    Get the approver ID for a nomination.

    When tenant_id is provided (authenticated endpoints) the query adds a
    tenant-scoping JOIN so a user from one tenant cannot probe another tenant's
    nominations.

    When tenant_id is omitted (email-action endpoint) the tenant filter is
    skipped; security is provided instead by the signed, time-limited JWT that
    must have been issued by this system.

    Returns: ApproverId, or None if not found.
    """
    with get_db_context() as session:
        if tenant_id is not None:
            row = session.execute(
                text("""
                    SELECT n.ApproverId
                    FROM Nominations n
                    JOIN Users u ON n.NominatorId = u.UserId
                    WHERE n.NominationId = :nomination_id
                      AND u.TenantId     = :tenant_id
                """),
                {"nomination_id": nomination_id, "tenant_id": tenant_id},
            ).fetchone()
        else:
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
                    n.Amount,
                    nominator.userEmail                                AS NominatorEmail,
                    nominator.FirstName + ' ' + nominator.LastName    AS NominatorName,
                    beneficiary.FirstName + ' ' + beneficiary.LastName AS BeneficiaryName,
                    beneficiary.userEmail                              AS BeneficiaryEmail,
                    approver.userEmail                                 AS ApproverEmail,
                    approver.FirstName + ' ' + approver.LastName      AS ApproverName,
                    n.NominationDescription,
                    n.Status,
                    nc.category_description                            AS CategoryDescription
                FROM dbo.Nominations n
                INNER JOIN dbo.Users nominator   ON n.NominatorId   = nominator.UserId
                INNER JOIN dbo.Users beneficiary ON n.BeneficiaryId = beneficiary.UserId
                INNER JOIN dbo.Users approver    ON n.ApproverId    = approver.UserId
                LEFT JOIN  dbo.nomination_categories nc ON nc.id    = n.CategoryId
                WHERE n.NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id},
        ).fetchone()

    if row:
        return {
            "nomination_id":        int(row[0]),
            "dollar_amount":        float(row[1]),
            "nominator_email":      row[2],
            "nominator_name":       row[3],
            "beneficiary_name":     row[4],
            "beneficiary_email":    row[5],
            "approver_email":       row[6],
            "approver_name":        row[7],
            "description":          row[8],
            "status":               row[9],
            "category_description": row[10],   # None for tenants without categories
        }
    return None


def get_nomination_history(user_id: int, tenant_id: int) -> List[Tuple]:
    """
    Get nomination history for a user (as nominator), scoped to the tenant.
    Returns: List of (NominationId, NominatorId, BeneficiaryId, ApproverId,
                      Amount, Currency, NominationDescription, NominationDate,
                      ApprovedDate, PayedDate, Status, CategoryDescription)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT n.NominationId, n.NominatorId, n.BeneficiaryId, n.ApproverId,
                       n.Amount, n.Currency, n.NominationDescription, n.NominationDate,
                       n.ApprovedDate, n.PayedDate, n.Status,
                       nc.category_description AS CategoryDescription
                FROM Nominations n
                JOIN Users u ON n.NominatorId = u.UserId
                LEFT JOIN dbo.nomination_categories nc ON nc.id = n.CategoryId
                WHERE n.NominatorId = :user_id
                  AND u.TenantId    = :tenant_id
                ORDER BY n.NominationDate DESC
            """),
            {"user_id": user_id, "tenant_id": tenant_id},
        ).fetchall()


def get_nomination_for_payroll(nomination_id: int) -> Optional[Tuple]:
    """
    Get nomination details for payroll extract.
    Returns: (BeneficiaryId, Amount, NominationDate, FirstName, LastName)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT n.BeneficiaryId, n.Amount, n.NominationDate,
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


def mark_nomination_payment_submitted(nomination_id: int, payment_ref: str) -> bool:
    """
    Record that a payout has been submitted to Workday (or Workday_Proxy).
    Sets Status = 'PaymentSubmitted', stores the paymentRef returned by the
    POST /payouts call, and records the submission timestamp.
    Returns True if a row was updated.
    """
    with get_db_context() as session:
        result = session.execute(
            text("""
                UPDATE Nominations
                SET Status             = 'PaymentSubmitted',
                    PaymentRef         = :payment_ref,
                    PaymentSubmittedAt = GETUTCDATE()
                WHERE NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id, "payment_ref": payment_ref},
        )
        session.commit()
        return result.rowcount > 0


def mark_nomination_paid_by_ref(payment_ref: str) -> Optional[int]:
    """
    Mark a nomination as Paid using the paymentRef as the lookup key.
    Called by the webhook bridge when Workday_Proxy POSTs PayoutAccepted.
    Returns the NominationId that was updated, or None if not found.
    """
    with get_db_context() as session:
        row = session.execute(
            text("""
                UPDATE Nominations
                SET PayedDate = GETUTCDATE(), Status = 'Paid'
                OUTPUT INSERTED.NominationId
                WHERE PaymentRef = :payment_ref
                  AND Status     = 'PaymentSubmitted'
            """),
            {"payment_ref": payment_ref},
        ).fetchone()
        session.commit()
        return row[0] if row else None


def get_nomination_id_by_payment_ref(payment_ref: str) -> Optional[int]:
    """
    Look up a NominationId by its PaymentRef.
    Used to resolve the nomination before publishing the PayoutAccepted event.
    """
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT NominationId
                FROM   Nominations
                WHERE  PaymentRef = :payment_ref
            """),
            {"payment_ref": payment_ref},
        ).fetchone()
        return row[0] if row else None


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
                SELECT NominationId, BeneficiaryId, Amount, NominationDate
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
                SELECT NominationId, NominatorId, Amount, NominationDate
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


def get_beneficiary_descriptions(beneficiary_id: int) -> List[str]:
    """
    Return the NominationDescriptions from past nominations WRITTEN BY
    the beneficiary (i.e. where they were the nominator).

    Used by fraud_ml.py to build the beneficiary's "voice" embedding at
    inference time — the same logic add_semantic_features() uses during
    training.  Returns an empty list when the beneficiary has never made
    a nomination, in which case the semantic features fall back to neutral.
    """
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT TOP 20 NominationDescription
                FROM Nominations
                WHERE NominatorId = :beneficiary_id
                  AND NominationDescription IS NOT NULL
                  AND NominationDescription <> ''
                ORDER BY NominationDate DESC
            """),
            {"beneficiary_id": beneficiary_id},
        ).fetchall()
        return [row[0] for row in rows]


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


def get_overall_amount_stats(tenant_id: int) -> Tuple[float, float]:
    """
    Get mean and standard deviation of nomination amounts for a single tenant.

    Scoped to one tenant so that z-scores are meaningful within a currency.
    For example, KRW amounts (50 000–300 000) must never be averaged with
    USD amounts (50–300) — a mixed mean would make every Korean nomination
    look like a fraud outlier.
    """
    with get_db_context() as session:
        result = session.execute(
            text("""
                SELECT AVG(CAST(n.Amount AS FLOAT)) AS MeanAmount,
                       STDEV(CAST(n.Amount AS FLOAT)) AS StdAmount
                FROM dbo.Nominations n
                JOIN dbo.Users u ON u.UserId = n.NominatorId
                WHERE u.TenantId = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).fetchone()
        return result if result else (0.0, 0.0)


def save_p2p_fraud_score(
    nomination_id: int,
    fraud_score: int,
    risk_level: str,
    warning_flags: str,
) -> bool:
    """Persist the peer-to-peer fraud score at nomination submission time."""
    with get_db_context() as session:
        result = session.execute(
            text("""
                MERGE dbo.P2P_FraudScores AS target
                USING (SELECT :nomination_id AS NominationId) AS src
                ON target.NominationId = src.NominationId
                WHEN MATCHED THEN
                    UPDATE SET FraudScore = :fraud_score,
                               RiskLevel  = :risk_level,
                               FraudFlags = :warning_flags
                WHEN NOT MATCHED THEN
                    INSERT (NominationId, FraudScore, RiskLevel, FraudFlags)
                    VALUES (:nomination_id, :fraud_score, :risk_level, :warning_flags);
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

def get_category_breakdown(tenant_id: int) -> List[Tuple]:
    """
    Return nomination counts and total spend per category for a tenant.

    Only includes nominations that have a CategoryId set (tenants without
    categories configured return an empty list).

    Returns: List of (category_description, nomination_count, total_amount, avg_amount)
    ordered by nomination_count DESC.
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT
                    nc.category_description,
                    COUNT(n.NominationId)          AS NominationCount,
                    SUM(n.Amount)                  AS TotalAmount,
                    AVG(CAST(n.Amount AS FLOAT))   AS AvgAmount
                FROM dbo.Nominations n
                JOIN dbo.Users u ON u.UserId = n.NominatorId
                JOIN dbo.nomination_categories nc ON nc.id = n.CategoryId
                WHERE u.TenantId = :tenant_id
                GROUP BY nc.category_description
                ORDER BY NominationCount DESC
            """),
            {"tenant_id": tenant_id},
        ).fetchall()


def get_analytics_overview(tenant_id: int) -> dict:
    """Get high-level analytics metrics for a tenant."""
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT
                    COUNT(*)                                                AS totalNominations,
                    SUM(n.Amount)                                     AS totalAmount,
                    SUM(CASE WHEN n.Status = 'Approved' THEN 1 ELSE 0 END) AS approvedCount,
                    SUM(CASE WHEN n.Status = 'Pending'  THEN 1 ELSE 0 END) AS pendingCount,
                    AVG(CAST(n.Amount AS FLOAT))                      AS avgAmount,
                    SUM(CASE WHEN n.Status = 'Rejected' THEN 1 ELSE 0 END) AS rejectedCount
                FROM Nominations n
                JOIN Users u ON n.NominatorId = u.UserId
                WHERE u.TenantId = :tenant_id
            """),
            {"tenant_id": tenant_id},
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


def get_spending_trends(tenant_id: int, days: int = 90) -> List[Tuple]:
    """Get spending trends over last N days for a tenant."""
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT
                    CAST(n.NominationDate AS DATE) AS PeriodDate,
                    COUNT(*)                       AS NominationCount,
                    SUM(n.Amount)            AS TotalAmount
                FROM Nominations n
                JOIN Users u ON n.NominatorId = u.UserId
                WHERE n.NominationDate >= DATEADD(DAY, :neg_days, CAST(GETDATE() AS DATE))
                  AND u.TenantId = :tenant_id
                GROUP BY CAST(n.NominationDate AS DATE)
                ORDER BY PeriodDate DESC
            """),
            {"neg_days": -abs(days), "tenant_id": tenant_id},
        ).fetchall()


def get_department_spending(tenant_id: int) -> List[Tuple]:
    """Get spending by department for a tenant."""
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT
                    u.Title                            AS Department,
                    COUNT(n.NominationId)              AS NominationCount,
                    SUM(n.Amount)                AS TotalSpent,
                    AVG(CAST(n.Amount AS FLOAT)) AS AvgAmount
                FROM Nominations n
                JOIN Users u ON n.BeneficiaryId = u.UserId
                WHERE n.Status IN ('Approved', 'Paid')
                  AND u.TenantId = :tenant_id
                GROUP BY u.Title
                ORDER BY TotalSpent DESC
            """),
            {"tenant_id": tenant_id},
        ).fetchall()


def get_top_recipients(tenant_id: int, limit: int = 10) -> List[Tuple]:
    """Get top recipients by count for a tenant."""
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT TOP (:limit)
                    u.UserId,
                    u.FirstName,
                    u.LastName,
                    COUNT(n.NominationId) AS NominationCount,
                    SUM(n.Amount)   AS TotalAmount
                FROM Nominations n
                JOIN Users u ON n.BeneficiaryId = u.UserId
                WHERE n.Status IN ('Approved', 'Paid')
                  AND u.TenantId = :tenant_id
                GROUP BY u.UserId, u.FirstName, u.LastName
                ORDER BY NominationCount DESC
            """),
            {"limit": limit, "tenant_id": tenant_id},
        ).fetchall()


def get_top_nominators(tenant_id: int, limit: int = 10) -> List[Tuple]:
    """Get top nominators by count for a tenant."""
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT TOP (:limit)
                    u.UserId,
                    u.FirstName,
                    u.LastName,
                    COUNT(n.NominationId) AS NominationCount,
                    SUM(n.Amount)   AS TotalAmount
                FROM Nominations n
                JOIN Users u ON n.NominatorId = u.UserId
                WHERE n.Status IN ('Approved', 'Paid')
                  AND u.TenantId = :tenant_id
                GROUP BY u.UserId, u.FirstName, u.LastName
                ORDER BY NominationCount DESC
            """),
            {"limit": limit, "tenant_id": tenant_id},
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
                    SUM(n.Amount)   AS TotalAmount
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
                    SUM(n.Amount)   AS TotalAmount
                FROM Nominations n
                JOIN Users u ON n.NominatorId = u.UserId
                WHERE n.Status IN ('Approved', 'Paid')
                  AND u.Title = :department
                GROUP BY u.UserId, u.FirstName, u.LastName
                ORDER BY NominationCount DESC
            """),
            {"limit": limit, "department": department},
        ).fetchall()


def get_fraud_alerts(tenant_id: int, limit: int = 20) -> List[Tuple]:
    """Get recent P2P fraud alerts for a tenant."""
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
                    n.Amount,
                    n.NominationDate
                FROM dbo.P2P_FraudScores fs
                JOIN Nominations n     ON fs.NominationId  = n.NominationId
                JOIN Users nominator   ON n.NominatorId    = nominator.UserId
                JOIN Users beneficiary ON n.BeneficiaryId  = beneficiary.UserId
                WHERE fs.RiskLevel IN ('HIGH', 'MEDIUM')
                  AND nominator.TenantId = :tenant_id
                ORDER BY n.NominationDate DESC
            """),
            {"limit": limit, "tenant_id": tenant_id},
        ).fetchall()


def get_approval_metrics(tenant_id: int) -> dict:
    """Get approval/rejection metrics for a tenant."""
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT
                    COUNT(*)  AS TotalNominations,
                    SUM(CASE WHEN n.Status = 'Approved' THEN 1 ELSE 0 END) AS ApprovedCount,
                    SUM(CASE WHEN n.Status = 'Rejected' THEN 1 ELSE 0 END) AS RejectedCount,
                    AVG(CAST(DATEDIFF(DAY, n.NominationDate, n.ApprovedDate) AS FLOAT)) AS AvgDaysToApproval
                FROM Nominations n
                JOIN Users u ON n.NominatorId = u.UserId
                WHERE n.ApprovedDate IS NOT NULL
                  AND u.TenantId = :tenant_id
            """),
            {"tenant_id": tenant_id},
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


def get_diversity_metrics(tenant_id: int) -> dict:
    """Calculate diversity metrics for award distribution within a tenant."""
    with get_db_context() as session:
        # Single session for all three queries – avoids three round-trips
        summary_row = session.execute(
            text("""
                SELECT
                    COUNT(DISTINCT n.BeneficiaryId) AS UniqueRecipients,
                    COUNT(*)                        AS TotalNominations
                FROM Nominations n
                JOIN Users u ON n.NominatorId = u.UserId
                WHERE n.Status IN ('Approved', 'Paid')
                  AND u.TenantId = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).fetchone()
        unique_recipients = summary_row[0] or 1
        total_nominations = summary_row[1] or 1

        # Individual recipient counts for Gini coefficient
        counts = [
            row[0]
            for row in session.execute(
                text("""
                    SELECT COUNT(*) AS RecipientCount
                    FROM Nominations n
                    JOIN Users u ON n.NominatorId = u.UserId
                    WHERE n.Status IN ('Approved', 'Paid')
                      AND u.TenantId = :tenant_id
                    GROUP BY n.BeneficiaryId
                """),
                {"tenant_id": tenant_id},
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
                FROM Nominations n
                JOIN Users u ON n.NominatorId = u.UserId
                WHERE n.Status IN ('Approved', 'Paid')
                  AND u.TenantId = :tenant_id
                GROUP BY n.BeneficiaryId
                ORDER BY COUNT(*) DESC
            """),
            {"tenant_id": tenant_id},
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
# INTEGRITY / GRAPH PATTERN FINDINGS
# ===========================================================================

def get_integrity_runs(tenant_id: int) -> list[dict]:
    """
    Return the list of distinct weekly job runs for a tenant,
    ordered most-recent first.  Each entry is the first DetectedAt
    timestamp for that RunId, used as the run label in the UI.
    """
    with get_db_context() as session:
        rows = session.execute(text("""
            SELECT   RunId,
                     MIN(DetectedAt)  AS RunDate,
                     COUNT(*)         AS TotalFindings
            FROM     dbo.GraphPatternFindings
            WHERE    TenantId = :tid
            GROUP BY RunId
            ORDER BY MIN(DetectedAt) DESC
        """), {"tid": tenant_id}).fetchall()
        return [
            {
                "runId":         row[0],
                "runDate":       row[1].isoformat() if row[1] else None,
                "totalFindings": row[2],
            }
            for row in rows
        ]


def get_integrity_findings(tenant_id: int, run_id: str) -> list[dict]:
    """
    Return all findings for a specific RunId, ordered by severity then type.
    AffectedUsers and NominationIds are returned as raw JSON strings for the
    frontend to parse.
    """
    with get_db_context() as session:
        rows = session.execute(text("""
            SELECT FindingId, PatternType, Severity,
                   AffectedUsers, NominationIds, Detail, DetectedAt, TotalAmount
            FROM   dbo.GraphPatternFindings
            WHERE  TenantId = :tid
              AND  RunId    = :run_id
            ORDER BY
                CASE Severity
                    WHEN 'Critical' THEN 1
                    WHEN 'High'     THEN 2
                    WHEN 'Medium'   THEN 3
                    ELSE 4
                END,
                PatternType
        """), {"tid": tenant_id, "run_id": run_id}).fetchall()
        return [
            {
                "findingId":     row[0],
                "patternType":   row[1],
                "severity":      row[2],
                "affectedUsers": row[3],   # JSON string — parsed by frontend
                "nominationIds": row[4],   # JSON string — parsed by frontend
                "detail":        row[5],
                "detectedAt":    row[6].isoformat() if row[6] else None,
                "totalAmount":   row[7],
            }
            for row in rows
        ]


# ===========================================================================
# FINDING EXPORT
# ===========================================================================

def get_finding_with_nominations(finding_id: int, tenant_id: int) -> dict | None:
    """
    Return a finding row plus all its associated nominations (with resolved
    user names) for Excel export.

    Returns None if the finding does not exist or belongs to a different tenant.
    The 'nominations' list is in raw DB order — callers are responsible for
    applying pattern-specific ordering (e.g. ring-cycle ordering).
    """
    with get_db_context() as session:
        # ── Finding metadata ──────────────────────────────────────────────
        finding_row = session.execute(text("""
            SELECT FindingId, PatternType, Severity,
                   AffectedUsers, NominationIds, Detail, DetectedAt, TotalAmount
            FROM   dbo.GraphPatternFindings
            WHERE  FindingId = :fid
              AND  TenantId  = :tid
        """), {"fid": finding_id, "tid": tenant_id}).fetchone()

        if not finding_row:
            return None

        import json as _json
        nomination_ids: list[int] = _json.loads(finding_row[4] or "[]")
        if not nomination_ids:
            nominations = []
        else:
            # Build a parameterised IN clause — SQL Server safe approach
            placeholders = ", ".join(f":nid_{i}" for i in range(len(nomination_ids)))
            params = {"tid": tenant_id}
            params.update({f"nid_{i}": v for i, v in enumerate(nomination_ids)})

            nom_rows = session.execute(text(f"""
                SELECT
                    n.NominationId,
                    n.NominatorId,
                    n.BeneficiaryId,
                    n.ApproverId,
                    ISNULL(un.FirstName, '') + ' ' + ISNULL(un.LastName, '')  AS NominatorName,
                    ISNULL(ub.FirstName, '') + ' ' + ISNULL(ub.LastName, '')  AS BeneficiaryName,
                    ISNULL(ua.FirstName, '') + ' ' + ISNULL(ua.LastName, '')  AS ApproverName,
                    n.Amount,
                    n.Currency,
                    n.NominationDescription,
                    n.Status,
                    n.NominationDate
                FROM   dbo.Nominations  n
                JOIN   dbo.Users        un ON un.UserId = n.NominatorId
                JOIN   dbo.Users        ub ON ub.UserId = n.BeneficiaryId
                LEFT JOIN dbo.Users     ua ON ua.UserId = n.ApproverId
                WHERE  n.NominationId IN ({placeholders})
                  AND  un.TenantId = :tid
            """), params).fetchall()

            nominations = [
                {
                    "nominationId":    r[0],
                    "nominatorId":     r[1],
                    "beneficiaryId":   r[2],
                    "approverId":      r[3],
                    "nominatorName":   r[4].strip(),
                    "beneficiaryName": r[5].strip(),
                    "approverName":    r[6].strip(),
                    "amount":          float(r[7]) if r[7] is not None else 0.0,
                    "currency":        r[8] or "",
                    "description":     r[9] or "",
                    "status":          r[10] or "",
                    "nominationDate":  r[11].isoformat()[:10] if r[11] else "",
                }
                for r in nom_rows
            ]

    return {
        "findingId":   finding_row[0],
        "patternType": finding_row[1],
        "severity":    finding_row[2],
        "detail":      finding_row[5] or "",
        "detectedAt":  finding_row[6].isoformat() if finding_row[6] else "",
        "totalAmount": float(finding_row[7]) if finding_row[7] is not None else 0.0,
        "nominations": nominations,
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


# ===========================================================================
# ASK CONVERSATIONS
# ===========================================================================

def create_conversation(
    conversation_id: str,
    user_id:         int,
    tenant_id:       int,
    title:           str,
) -> None:
    """Insert a new conversation row."""
    with get_db_context() as session:
        session.execute(text("""
            INSERT INTO dbo.AskConversations
                (ConversationId, UserId, TenantId, Title, CreatedAt, UpdatedAt)
            VALUES
                (:cid, :uid, :tid, :title, GETUTCDATE(), GETUTCDATE())
        """), {"cid": conversation_id, "uid": user_id,
               "tid": tenant_id,       "title": title[:200]})
        session.commit()


def append_message(
    conversation_id: str,
    role:            str,
    content:         str,
    export_json:     str | None = None,
) -> None:
    """Append a single message to a conversation and bump UpdatedAt."""
    with get_db_context() as session:
        session.execute(text("""
            INSERT INTO dbo.AskMessages
                (ConversationId, Role, Content, ExportJson, CreatedAt)
            VALUES
                (:cid, :role, :content, :export, GETUTCDATE())
        """), {"cid": conversation_id, "role": role,
               "content": content,     "export": export_json})
        session.execute(text("""
            UPDATE dbo.AskConversations
            SET    UpdatedAt = GETUTCDATE()
            WHERE  ConversationId = :cid
        """), {"cid": conversation_id})
        session.commit()


def get_conversations(user_id: int, tenant_id: int, limit: int = 50) -> list[dict]:
    """Return the most recent conversations for a user, newest first."""
    with get_db_context() as session:
        rows = session.execute(text(f"""
            SELECT TOP {min(limit, 200)}
                   ConversationId, Title, CreatedAt, UpdatedAt
            FROM   dbo.AskConversations
            WHERE  UserId = :uid AND TenantId = :tid
            ORDER BY UpdatedAt DESC
        """), {"uid": user_id, "tid": tenant_id}).fetchall()
    return [
        {
            "conversationId": row[0],
            "title":          row[1],
            "createdAt":      row[2].isoformat() if row[2] else None,
            "updatedAt":      row[3].isoformat() if row[3] else None,
        }
        for row in rows
    ]


def get_messages(conversation_id: str, tenant_id: int) -> list[dict]:
    """
    Return all messages for a conversation, oldest first.
    Tenant check via JOIN prevents cross-tenant access.
    """
    with get_db_context() as session:
        rows = session.execute(text("""
            SELECT m.Role, m.Content, m.ExportJson, m.CreatedAt
            FROM   dbo.AskMessages      m
            JOIN   dbo.AskConversations c ON c.ConversationId = m.ConversationId
            WHERE  m.ConversationId = :cid AND c.TenantId = :tid
            ORDER BY m.MessageId
        """), {"cid": conversation_id, "tid": tenant_id}).fetchall()
    return [
        {
            "role":       row[0],
            "content":    row[1],
            "exportJson": row[2],
            "createdAt":  row[3].isoformat() if row[3] else None,
        }
        for row in rows
    ]


def delete_conversation(conversation_id: str, user_id: int, tenant_id: int) -> bool:
    """
    Delete a conversation and all its messages (CASCADE).
    Returns True if a row was deleted, False if not found / not owned.
    """
    with get_db_context() as session:
        result = session.execute(text("""
            DELETE FROM dbo.AskConversations
            WHERE  ConversationId = :cid
              AND  UserId   = :uid
              AND  TenantId = :tid
        """), {"cid": conversation_id, "uid": user_id, "tid": tenant_id})
        session.commit()
        return result.rowcount > 0


def rename_conversation(
    conversation_id: str,
    user_id: int,
    tenant_id: int,
    title: str,
) -> bool:
    """
    Update the Title of a conversation.
    Returns True if a row was updated, False if not found / not owned.
    """
    with get_db_context() as session:
        result = session.execute(text("""
            UPDATE dbo.AskConversations
               SET Title     = :title,
                   UpdatedAt = GETUTCDATE()
             WHERE ConversationId = :cid
               AND UserId   = :uid
               AND TenantId = :tid
        """), {"title": title[:200], "cid": conversation_id,
               "uid": user_id, "tid": tenant_id})
        session.commit()
        return result.rowcount > 0


# ===========================================================================
# DEMO SELF-REGISTRATION HELPERS
# ===========================================================================

DEMO_TENANT_NAME = "Terian Services Demo"


def get_demo_tenant_id() -> Optional[int]:
    """Return the internal TenantId for the Demo tenant, or None if not found."""
    with get_db_context() as session:
        row = session.execute(
            text("SELECT TenantId FROM dbo.Tenants WHERE TenantName = :name"),
            {"name": DEMO_TENANT_NAME},
        ).fetchone()
        return row[0] if row else None


def get_demo_aad_tenant_id() -> Optional[str]:
    """Return the Azure AD tenant GUID for the Demo tenant."""
    with get_db_context() as session:
        row = session.execute(
            text("SELECT AzureAdTenantId FROM dbo.Tenants WHERE TenantName = :name"),
            {"name": DEMO_TENANT_NAME},
        ).fetchone()
        return row[0] if row else None


def demo_email_registered(email: str) -> bool:
    """
    Return True if this email address has already been registered as a demo user
    in dbo.Users (permanent check, not time-windowed).

    Used in demo_router.py to short-circuit re-registration attempts before
    calling the Graph API or sending another invitation email.
    """
    with get_db_context() as session:
        row = session.execute(
            text(
                "SELECT 1 FROM dbo.Users u "
                "JOIN dbo.Tenants t ON u.TenantId = t.TenantId "
                "WHERE u.userEmail = :email AND t.TenantName = :demo_name"
            ),
            {"email": email, "demo_name": DEMO_TENANT_NAME},
        ).fetchone()
        return row is not None


def upn_exists_in_tenant(upn: str, tenant_id: int) -> bool:
    """Return True if a user with this UPN already exists in the given tenant."""
    with get_db_context() as session:
        row = session.execute(
            text(
                "SELECT 1 FROM dbo.Users "
                "WHERE userPrincipalName = :upn AND TenantId = :tid"
            ),
            {"upn": upn, "tid": tenant_id},
        ).fetchone()
        return row is not None


def log_demo_registration(
    first_name: str,
    last_name:  str,
    email:      str,
    is_admin:   bool,
    aad_object_id: Optional[str],
    request_ip: Optional[str],
) -> None:
    """Insert an audit row into dbo.DemoRegistrationRequests."""
    with get_db_context() as session:
        session.execute(
            text("""
                INSERT INTO dbo.DemoRegistrationRequests
                    (FirstName, LastName, Email, IsAdmin, AadObjectId, RequestIp)
                VALUES (:first, :last, :email, :is_admin, :oid, :ip)
            """),
            {
                "first":    first_name,
                "last":     last_name,
                "email":    email,
                "is_admin": 1 if is_admin else 0,
                "oid":      aad_object_id,
                "ip":       request_ip,
            },
        )
        session.commit()


def count_demo_registrations_by_email(email: str, since_minutes: int = 60) -> int:
    """Return how many invitations have been sent to this email in the last N minutes."""
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT COUNT(*) FROM dbo.DemoRegistrationRequests
                WHERE Email = :email
                  AND RequestedAt >= DATEADD(MINUTE, :neg_mins, GETUTCDATE())
            """),
            {"email": email, "neg_mins": -since_minutes},
        ).fetchone()
        return row[0] if row else 0


def count_demo_registrations_by_ip(ip: str, since_minutes: int = 60) -> int:
    """Return how many invitations have been sent from this IP in the last N minutes."""
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT COUNT(*) FROM dbo.DemoRegistrationRequests
                WHERE RequestIp = :ip
                  AND RequestedAt >= DATEADD(MINUTE, :neg_mins, GETUTCDATE())
            """),
            {"ip": ip, "neg_mins": -since_minutes},
        ).fetchone()
        return row[0] if row else 0


def create_demo_user(
    first_name: str,
    last_name: str,
    email: str,
    upn: str,
    tenant_id: int,
) -> int:
    """
    Insert a self-registered demo user into dbo.Users.

    Returns the new UserId.
    The user has no manager (NULL) and Title = 'Demo User'.
    """
    with get_db_context() as session:
        result = session.execute(
            text("""
                INSERT INTO dbo.Users
                    (userPrincipalName, userEmail, FirstName, LastName, Title, ManagerId, TenantId)
                OUTPUT INSERTED.UserId
                VALUES (:upn, :email, :first, :last, 'Demo User', NULL, :tid)
            """),
            {
                "upn":   upn,
                "email": email,
                "first": first_name,
                "last":  last_name,
                "tid":   tenant_id,
            },
        )
        row = result.fetchone()
        session.commit()
        return row[0]


# ===========================================================================
# HRBP REVIEW WORKFLOW
# ===========================================================================

def save_hrbp_fraud_flags(
    nomination_id:        int,
    fraud_score:          int,
    fraud_probability:    float,
    risk_level:           str,
    warning_flags:        str,
    top_features_json:    str | None = None,
    feature_summary_json: str | None = None,
) -> None:
    """
    Persist the P2P ML inference snapshot into dbo.HRBP_FraudFlags.

    Called at nomination-submission time when the P2P score triggers HRBP
    review, so the HRBP queue has full context without re-running inference.
    Idempotent via MERGE.
    """
    with get_db_context() as session:
        session.execute(
            text("""
                MERGE dbo.HRBP_FraudFlags AS target
                USING (SELECT :nomination_id AS NominationId) AS src
                ON target.NominationId = src.NominationId
                WHEN NOT MATCHED THEN
                    INSERT (NominationId, FraudScore, FraudProbability, RiskLevel,
                            WarningFlags, TopFeaturesJson, FeatureSummaryJson)
                    VALUES (:nomination_id, :fraud_score, :fraud_probability, :risk_level,
                            :warning_flags, :top_features_json, :feature_summary_json);
            """),
            {
                "nomination_id":        nomination_id,
                "fraud_score":          fraud_score,
                "fraud_probability":    fraud_probability,
                "risk_level":           risk_level,
                "warning_flags":        warning_flags,
                "top_features_json":    top_features_json,
                "feature_summary_json": feature_summary_json,
            },
        )
        session.commit()


def get_hrbp_fraud_flags(nomination_id: int) -> dict | None:
    """Return the HRBP_FraudFlags row for a nomination, or None if not found."""
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT FraudScore, FraudProbability, RiskLevel,
                       WarningFlags, TopFeaturesJson, FeatureSummaryJson, CreatedAt
                FROM dbo.HRBP_FraudFlags
                WHERE NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id},
        ).fetchone()
        if not row:
            return None
        return {
            "fraud_score":          row[0],
            "fraud_probability":    row[1],
            "risk_level":           row[2],
            "warning_flags":        row[3],
            "top_features_json":    row[4],
            "feature_summary_json": row[5],
            "created_at":           str(row[6]),
        }


def set_nomination_status(nomination_id: int, status: str) -> None:
    """Update the Status column of a single nomination row."""
    with get_db_context() as session:
        session.execute(
            text("""
                UPDATE dbo.Nominations
                SET    Status = :status
                WHERE  NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id, "status": status},
        )
        session.commit()


def get_hrbp_queue(tenant_id: int) -> list[dict]:
    """
    Return all nominations in PendingHRBPReview for a tenant, joined with
    nominator / beneficiary names and the FraudFlags snapshot.
    Ordered by submission time ascending (oldest first).
    """
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT
                    n.NominationId,
                    n.Status,
                    n.Amount,
                    n.Currency,
                    n.NominationDescription,
                    n.NominationDate,
                    nom.FirstName + ' ' + nom.LastName  AS NominatorName,
                    nom.userEmail                        AS NominatorEmail,
                    ben.FirstName + ' ' + ben.LastName  AS BeneficiaryName,
                    ben.userEmail                        AS BeneficiaryEmail,
                    ff.FraudScore,
                    ff.FraudProbability,
                    ff.RiskLevel,
                    ff.WarningFlags,
                    ff.TopFeaturesJson,
                    ff.FeatureSummaryJson
                FROM  dbo.Nominations n
                JOIN  dbo.Users nom ON nom.UserId      = n.NominatorId
                JOIN  dbo.Users ben ON ben.UserId      = n.BeneficiaryId
                LEFT JOIN dbo.HRBP_FraudFlags ff ON ff.NominationId = n.NominationId
                WHERE n.Status    = 'PendingHRBPReview'
                  AND nom.TenantId = :tenant_id
                ORDER BY n.NominationDate ASC
            """),
            {"tenant_id": tenant_id},
        ).fetchall()
        return [
            {
                "nomination_id":      r[0],
                "status":             r[1],
                "amount":             r[2],
                "currency":           r[3],
                "description":        r[4],
                "nomination_date":    str(r[5]),
                "nominator_name":     r[6],
                "nominator_email":    r[7],
                "beneficiary_name":   r[8],
                "beneficiary_email":  r[9],
                "fraud_score":        r[10],
                "fraud_probability":  r[11],
                "risk_level":         r[12],
                "warning_flags":      r[13].split(", ") if r[13] else [],
                "top_features":       r[14],
                "feature_summary":    r[15],
            }
            for r in rows
        ]


def get_user_roles(user_id: int) -> list[str]:
    """Return all Role values assigned to a user in dbo.UserRoles."""
    with get_db_context() as session:
        rows = session.execute(
            text("SELECT Role FROM dbo.UserRoles WHERE UserId = :uid"),
            {"uid": user_id},
        ).fetchall()
        return [r[0] for r in rows]


def assign_user_role(user_id: int, role: str, assigned_by: int) -> bool:
    """
    Grant *role* to *user_id*.  Idempotent — silently succeeds if already assigned.
    Returns True if newly inserted, False if already existed.
    """
    with get_db_context() as session:
        result = session.execute(
            text("""
                MERGE dbo.UserRoles AS target
                USING (SELECT :uid AS UserId, :role AS Role) AS src
                ON target.UserId = src.UserId AND target.Role = src.Role
                WHEN NOT MATCHED THEN
                    INSERT (UserId, Role, AssignedBy)
                    VALUES (:uid, :role, :assigned_by);
            """),
            {"uid": user_id, "role": role, "assigned_by": assigned_by},
        )
        session.commit()
        return result.rowcount > 0


def revoke_user_role(user_id: int, role: str) -> bool:
    """Remove a role assignment. Returns True if a row was deleted."""
    with get_db_context() as session:
        result = session.execute(
            text("""
                DELETE FROM dbo.UserRoles
                WHERE UserId = :uid AND Role = :role
            """),
            {"uid": user_id, "role": role},
        )
        session.commit()
        return result.rowcount > 0


def get_hrbp_users(tenant_id: int) -> list[dict]:
    """
    Return all users with the HRBP role for a given tenant.
    Used by the auxiliary service to email the right people when a
    nomination is flagged.
    """
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT u.UserId,
                       u.FirstName + ' ' + u.LastName AS FullName,
                       u.userEmail
                FROM   dbo.UserRoles ur
                JOIN   dbo.Users u ON u.UserId = ur.UserId
                WHERE  ur.Role      = 'HRBP'
                  AND  u.TenantId   = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).fetchall()
        return [
            {"user_id": r[0], "full_name": r[1], "email": r[2]}
            for r in rows
        ]


def get_sla_breached_nominations(sla_hours: int) -> list[dict]:
    """
    Return all nominations in PendingHRBPReview whose NominationDate is
    older than *sla_hours* hours.  Called by the Logic App internal endpoint.
    """
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT n.NominationId,
                       n.TenantId,
                       n.NominationDate,
                       nom.FirstName + ' ' + nom.LastName AS NominatorName,
                       ben.FirstName + ' ' + ben.LastName AS BeneficiaryName,
                       ff.RiskLevel
                FROM   dbo.Nominations n
                JOIN   dbo.Users nom ON nom.UserId = n.NominatorId
                JOIN   dbo.Users ben ON ben.UserId = n.BeneficiaryId
                LEFT JOIN dbo.HRBP_FraudFlags ff ON ff.NominationId = n.NominationId
                WHERE  n.Status = 'PendingHRBPReview'
                  AND  n.NominationDate < DATEADD(HOUR, :neg_hours, GETUTCDATE())
                ORDER  BY n.NominationDate ASC
            """),
            {"neg_hours": -sla_hours},
        ).fetchall()
        return [
            {
                "nomination_id":   r[0],
                "tenant_id":       r[1],
                "nomination_date": str(r[2]),
                "nominator_name":  r[3],
                "beneficiary_name": r[4],
                "risk_level":      r[5],
            }
            for r in rows
        ]


def get_nomination_details_for_hrbp(nomination_id: int) -> dict | None:
    """
    Full nomination detail for the HRBP approval / rejection flow,
    including nominator info, beneficiary info, and fraud flags.
    """
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT
                    n.NominationId,
                    nom.TenantId,
                    n.Amount,
                    n.Currency,
                    n.NominationDescription,
                    n.NominationDate,
                    n.Status,
                    n.ApproverId,
                    nom.FirstName + ' ' + nom.LastName AS NominatorName,
                    nom.userEmail                       AS NominatorEmail,
                    ben.FirstName + ' ' + ben.LastName AS BeneficiaryName,
                    ben.userEmail                       AS BeneficiaryEmail,
                    ff.FraudScore,
                    ff.RiskLevel,
                    ff.WarningFlags,
                    nom.UserId                          AS NominatorId,
                    ben.UserId                          AS BeneficiaryId
                FROM  dbo.Nominations n
                JOIN  dbo.Users nom ON nom.UserId = n.NominatorId
                JOIN  dbo.Users ben ON ben.UserId = n.BeneficiaryId
                LEFT JOIN dbo.HRBP_FraudFlags ff ON ff.NominationId = n.NominationId
                WHERE n.NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id},
        ).fetchone()
        if not row:
            return None
        return {
            "nomination_id":    row[0],
            "tenant_id":        row[1],
            "amount":           row[2],
            "currency":         row[3],
            "description":      row[4],
            "nomination_date":  str(row[5]),
            "status":           row[6],
            "approver_id":      row[7],
            "nominator_name":   row[8],
            "nominator_email":  row[9],
            "beneficiary_name": row[10],
            "beneficiary_email": row[11],
            "fraud_score":      row[12],
        }