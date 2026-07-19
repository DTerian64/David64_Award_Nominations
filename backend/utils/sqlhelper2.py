"""
sqlhelper2.py – SQLAlchemy-based database helper
=================================================
Drop-in replacement for sqlhelper.py (pyodbc/ODBC).

Authentication: Entra token via DefaultAzureCredential -- the container's
Managed Identity in Azure (selected by MI_CLIENT_ID), or your az / VS Code
login locally. No SQL username/password.

Schema ownership
----------------
The database schema is owned by the standalone `schema-migration` project
(Alembic) and applied via its pipeline (ADR-0001). This module holds the engine,
session helpers, and raw-SQL query functions only — it defines no ORM models and
does not create or alter tables.

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

from utils.audit_context import get_actor

# ---------------------------------------------------------------------------
# Environment / configuration  (mirrors sqlhelper.py)
# ---------------------------------------------------------------------------
DB_SERVER   = os.getenv("SQL_SERVER")
DB_NAME     = os.getenv("SQL_DATABASE")
DB_DRIVER   = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")



# ===========================================================================
# Engine factory
# ===========================================================================

def _build_engine():
    """
    SQLAlchemy engine using an Entra token via DefaultAzureCredential.
    DefaultAzureCredential resolves the container's user-assigned MI (selected by
    MI_CLIENT_ID) in Azure, or the developer's az / VS Code login locally.
    NullPool: access tokens expire, so a fresh one is fetched per connection.
    """
    from azure.identity import DefaultAzureCredential

    credential    = DefaultAzureCredential(
    managed_identity_client_id=os.getenv("MI_CLIENT_ID")
)
    base_conn_str = (
        f"Driver={{{DB_DRIVER}}};"
        f"Server={DB_SERVER};"
        f"Database={DB_NAME};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no;"
    )
    SQL_COPT_SS_ACCESS_TOKEN = 1256

    def _creator():
        import pyodbc
        token        = credential.get_token("https://database.windows.net/.default").token
        token_bytes  = token.encode("UTF-16-LE")
        token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
        return pyodbc.connect(base_conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})

    return create_engine("mssql+pyodbc://", creator=_creator, poolclass=NullPool)


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

    Returns: List of (id, category_description, min_amount, max_amount)
    An empty list means the tenant has no custom categories — the nomination
    form should show no category field at all.
    """
    with get_db_context() as session:
        rows = session.execute(
            text(
                "SELECT id, category_description, min_amount, max_amount "
                "FROM dbo.nomination_categories "
                "WHERE tenant_id = :tid AND is_active = 1 "
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


@dataclass
class CertificateConfig:
    """
    Per-tenant award-certificate configuration, read from
    dbo.Tenants.certificate_config (NVARCHAR(MAX) JSON).

    NULL column → all defaults → feature OFF.  Existing tenants are unaffected
    until they explicitly opt in, so every default here is the safe/off value.

    enabled               — master switch for the whole certificate feature
                            (the My Approvals certificate link AND the email
                            attachment).  False = no certificate anywhere.
    attach_to_beneficiary — when True, the beneficiary's approval email carries
                            the certificate PDF as an attachment.  Requires
                            enabled=True to have any effect.
    template_blob         — blob name (in the certificate-templates container)
                            of the background template to overlay.
    """
    enabled:               bool = False
    attach_to_beneficiary: bool = False
    template_blob:         str  = "default_certificate.png"


def get_tenant_certificate_config(tenant_id: int) -> CertificateConfig:
    """
    Load certificate_config JSON from dbo.Tenants and return a CertificateConfig.

    Missing keys fall back to dataclass defaults.  A NULL column or any JSON
    parse error returns a fully-defaulted (feature-off) config — never raises.
    """
    with get_db_context() as session:
        row = session.execute(
            text("SELECT certificate_config FROM dbo.Tenants WHERE TenantId = :tid"),
            {"tid": tenant_id},
        ).fetchone()

    raw = row[0] if row else None
    if not raw:
        return CertificateConfig()

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "Invalid JSON in certificate_config for tenant %d — using defaults",
            tenant_id,
        )
        return CertificateConfig()

    return CertificateConfig(
        enabled=bool(data.get("enabled", CertificateConfig.enabled)),
        attach_to_beneficiary=bool(
            data.get("attach_to_beneficiary", CertificateConfig.attach_to_beneficiary)
        ),
        template_blob=str(data.get("template_blob", CertificateConfig.template_blob)),
    )


# ===========================================================================
# EMAIL / CERTIFICATE TEMPLATES  (dbo.EmailTemplates)
# ===========================================================================

DEFAULT_TEMPLATE_TENANT_ID = 1   # canonical org holding the system default rows


def get_tenant_lang(tenant_id: int) -> str:
    """Tenant base language code ('en', 'ko', ...) from Config.locale; 'en' on any miss."""
    raw = get_tenant_config(tenant_id)
    if not raw:
        return "en"
    try:
        locale = json.loads(raw).get("locale") or "en"
    except (json.JSONDecodeError, TypeError):
        return "en"
    return (locale.split("-")[0].lower() or "en")


def get_email_template_candidates(template_key: str, tenant_id: int, lang: str) -> List[Tuple]:
    """Active template rows for the resolver to rank: this tenant + the default
    tenant, in this language + English. Returns (TenantId, Lang, Subject, BodyTemplate)."""
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT TenantId, Lang, Subject, BodyTemplate
                FROM dbo.EmailTemplates
                WHERE TemplateKey = :key
                  AND Active = 1
                  AND TenantId IN (:tid, :default_tid)
                  AND Lang     IN (:lang, 'en')
            """),
            {"key": template_key, "tid": tenant_id,
             "default_tid": DEFAULT_TEMPLATE_TENANT_ID, "lang": lang},
        ).fetchall()


def _email_template_row(r) -> dict:
    return {
        "template_id":  r[0],
        "template_key": r[1],
        "lang":         r[2],
        "subject":      r[3],
        "body_template": r[4],
        "active":       bool(r[5]),
        "version":      r[6],
        "updated_at":   (r[7].isoformat() + "Z") if r[7] else None,
        "updated_by":   r[8],
    }


def get_tenant_email_templates(tenant_id: int) -> List[dict]:
    """The tenant's OWN email/certificate template rows (not inherited defaults),
    ordered by key then language."""
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT TemplateId, TemplateKey, Lang, Subject, BodyTemplate,
                       Active, Version, updated_at, updated_by
                FROM   dbo.EmailTemplates
                WHERE  TenantId = :tid
                ORDER  BY TemplateKey, Lang
            """),
            {"tid": tenant_id},
        ).fetchall()
    return [_email_template_row(r) for r in rows]


def email_template_in_tenant(template_id: int, tenant_id: int) -> bool:
    """True if the template row exists and belongs to this tenant."""
    with get_db_context() as session:
        return session.execute(
            text("SELECT 1 FROM dbo.EmailTemplates "
                 "WHERE TemplateId = :id AND TenantId = :tid"),
            {"id": template_id, "tid": tenant_id},
        ).fetchone() is not None


def update_email_template(template_id: int, tenant_id: int, subject,
                          body_template: str, actor: str) -> Optional[dict]:
    """Update Subject + BodyTemplate for one of the tenant's own templates.
    Bumps Version and stamps updated_at/updated_by. Returns the updated row, or
    None if the template does not belong to the tenant (own-tenant guard)."""
    with get_db_context() as session:
        result = session.execute(
            text("""
                UPDATE dbo.EmailTemplates
                SET    Subject      = :subject,
                       BodyTemplate = :body,
                       Version      = Version + 1,
                       updated_at   = SYSUTCDATETIME(),
                       updated_by   = :actor
                WHERE  TemplateId = :id AND TenantId = :tid
            """),
            {"subject": subject, "body": body_template, "actor": actor,
             "id": template_id, "tid": tenant_id},
        )
        session.commit()
        if result.rowcount == 0:
            return None
        row = session.execute(
            text("""
                SELECT TemplateId, TemplateKey, Lang, Subject, BodyTemplate,
                       Active, Version, updated_at, updated_by
                FROM   dbo.EmailTemplates WHERE TemplateId = :id
            """),
            {"id": template_id},
        ).fetchone()
    return _email_template_row(row)


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
                ORDER BY FirstName, LastName
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
                     CategoryId, created_at, created_by, updated_at, updated_by)
                OUTPUT INSERTED.NominationId
                VALUES (:nominator_id, :beneficiary_id, :approver_id, :amount, :currency,
                        :description, GETDATE(), :initial_status, NULL, NULL,
                        :category_id, GETDATE(), :audit_by, GETDATE(), :audit_by)
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
                "audit_by":       get_actor(),
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


def get_decided_nominations_for_approver(approver_id: int, tenant_id: int) -> List[Tuple]:
    """
    Get nominations this approver has already decided (Approved or Rejected),
    scoped to the tenant. Powers the "Approved / Rejected" view of the
    My Approvals tab.

    Returns: List of (NominationId, NominatorId, BeneficiaryId, ApproverId,
                      Amount, Currency, NominationDescription, NominationDate,
                      ApprovedDate, PayedDate, Status, CategoryDescription,
                      RejectionReason, RejectionActor)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT n.NominationId, n.NominatorId, n.BeneficiaryId, n.ApproverId,
                       n.Amount, n.Currency, n.NominationDescription, n.NominationDate,
                       n.ApprovedDate, n.PayedDate, n.Status,
                       nc.category_description AS CategoryDescription,
                       n.RejectionReason, n.RejectionActor
                FROM Nominations n
                JOIN Users u ON n.NominatorId = u.UserId
                LEFT JOIN dbo.nomination_categories nc ON nc.id = n.CategoryId
                WHERE n.ApproverId = :approver_id
                  AND n.Status IN ('Approved', 'Paid', 'Rejected')
                  AND u.TenantId   = :tenant_id
                ORDER BY n.ApprovedDate DESC, n.NominationDate DESC
            """),
            {"approver_id": approver_id, "tenant_id": tenant_id},
        ).fetchall()


def get_nomination_for_certificate(nomination_id: int) -> Optional[dict]:
    """
    Fetch the fields needed to render an award certificate.

    Returns a dict (or None if the nomination does not exist) with:
      nomination_id, beneficiary_name, nominator_name, approver_name,
      amount, currency, category_description, status, approved_date, tenant_id
    """
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT
                    n.NominationId,
                    beneficiary.FirstName + ' ' + beneficiary.LastName AS BeneficiaryName,
                    nominator.FirstName   + ' ' + nominator.LastName   AS NominatorName,
                    approver.FirstName    + ' ' + approver.LastName    AS ApproverName,
                    n.Amount,
                    n.Currency,
                    nc.category_description                            AS CategoryDescription,
                    n.Status,
                    n.ApprovedDate,
                    nominator.TenantId                                 AS TenantId
                FROM dbo.Nominations n
                INNER JOIN dbo.Users nominator   ON n.NominatorId   = nominator.UserId
                INNER JOIN dbo.Users beneficiary ON n.BeneficiaryId = beneficiary.UserId
                INNER JOIN dbo.Users approver    ON n.ApproverId    = approver.UserId
                LEFT JOIN  dbo.nomination_categories nc ON nc.id    = n.CategoryId
                WHERE n.NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id},
        ).fetchone()

    if not row:
        return None

    return {
        "nomination_id":        int(row[0]),
        "beneficiary_name":     row[1],
        "nominator_name":       row[2],
        "approver_name":        row[3],
        "amount":               float(row[4]),
        "currency":             row[5],
        "category_description": row[6],
        "status":               row[7],
        "approved_date":        row[8],
        "tenant_id":            int(row[9]),
    }


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
                SET ApprovedDate = GETDATE(), Status = 'Approved',
                    updated_at = SYSUTCDATETIME(), updated_by = :audit_by
                WHERE NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id, "audit_by": get_actor()},
        )
        session.commit()
        return result.rowcount > 0


def reject_nomination(
    nomination_id: int,
    reason: str = "",
    actor: str = "Manager",
) -> bool:
    """
    Reject a nomination by setting Status to 'Rejected' and persisting the
    rejection reason and actor.

    Args:
        nomination_id: The nomination to reject.
        reason:        Free-text explanation shown to the nominator.
                       Empty string is stored as NULL.
        actor:         Who/what performed the rejection.
                       Conventional values: "Manager", "HRBP Review",
                       "Fraud Detection".

    Returns: True if a row was updated, False if nomination not found.
    """
    with get_db_context() as session:
        result = session.execute(
            text("""
                UPDATE dbo.Nominations
                SET Status          = 'Rejected',
                    RejectionReason = :reason,
                    RejectionActor  = :actor,
                    updated_at      = SYSUTCDATETIME(),
                    updated_by      = :audit_by
                WHERE NominationId  = :nomination_id
            """),
            {
                "nomination_id": nomination_id,
                "reason":        reason.strip() or None,
                "actor":         actor,
                "audit_by":      get_actor(),
            },
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
                      ApprovedDate, PayedDate, Status, CategoryDescription,
                      RejectionReason, RejectionActor)
    """
    with get_db_context() as session:
        return session.execute(
            text("""
                SELECT n.NominationId, n.NominatorId, n.BeneficiaryId, n.ApproverId,
                       n.Amount, n.Currency, n.NominationDescription, n.NominationDate,
                       n.ApprovedDate, n.PayedDate, n.Status,
                       nc.category_description AS CategoryDescription,
                       n.RejectionReason, n.RejectionActor
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
                SET PayedDate = GETDATE(), Status = 'Paid',
                    updated_at = SYSUTCDATETIME(), updated_by = :audit_by
                WHERE NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id, "audit_by": get_actor()},
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
                    PaymentSubmittedAt = GETUTCDATE(),
                    updated_at         = SYSUTCDATETIME(),
                    updated_by         = :audit_by
                WHERE NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id, "payment_ref": payment_ref, "audit_by": get_actor()},
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
                SET PayedDate = GETUTCDATE(), Status = 'Paid',
                    updated_at = SYSUTCDATETIME(), updated_by = :audit_by
                OUTPUT INSERTED.NominationId
                WHERE PaymentRef = :payment_ref
                  AND Status     = 'PaymentSubmitted'
            """),
            {"payment_ref": payment_ref, "audit_by": get_actor()},
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


def get_pair_nomination_history(
    nominator_id: int,
    beneficiary_id: int,
    tenant_id: int,
    exclude_nomination_id: int,
) -> list[dict]:
    """
    Return all nominations between *nominator_id* and *beneficiary_id* in
    either direction, within *tenant_id*, excluding the nomination currently
    under HRBP review.

    Both A→B and B→A nominations are included so the HRBP reviewer sees the
    full relationship history between the two people.  Each row carries
    nominator_name and beneficiary_name so the frontend can render direction.

    Returns list of dicts ordered newest-first.
    """
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT
                    n.NominationId,
                    n.Amount,
                    n.Currency,
                    n.NominationDescription,
                    n.NominationDate,
                    n.Status,
                    nc.category_description          AS CategoryDescription,
                    ff.RiskLevel,
                    nom.FirstName + ' ' + nom.LastName  AS NominatorName,
                    ben.FirstName + ' ' + ben.LastName  AS BeneficiaryName
                FROM  dbo.Nominations n
                JOIN  dbo.Users nom ON nom.UserId = n.NominatorId
                JOIN  dbo.Users ben ON ben.UserId = n.BeneficiaryId
                LEFT JOIN dbo.nomination_categories nc ON nc.id = n.CategoryId
                LEFT JOIN dbo.HRBP_FraudFlags ff ON ff.NominationId = n.NominationId
                WHERE nom.TenantId = :tenant_id
                  AND n.NominationId != :exclude_id
                  AND (
                      (n.NominatorId = :nominator_id   AND n.BeneficiaryId = :beneficiary_id)
                   OR (n.NominatorId = :beneficiary_id AND n.BeneficiaryId = :nominator_id)
                  )
                ORDER BY n.NominationDate DESC
            """),
            {
                "nominator_id":   nominator_id,
                "beneficiary_id": beneficiary_id,
                "tenant_id":      tenant_id,
                "exclude_id":     exclude_nomination_id,
            },
        ).fetchall()
        return [
            {
                "nomination_id":    r[0],
                "amount":           r[1],
                "currency":         r[2],
                "description":      r[3],
                "nomination_date":  str(r[4]),
                "status":           r[5],
                "category":         r[6],
                "risk_level":       r[7],
                "nominator_name":   r[8],
                "beneficiary_name": r[9],
            }
            for r in rows
        ]


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


def upsert_p2p_fraud_label(
    nomination_id: int,
    is_fraud: bool,
    confirmed_by: str = "HRBP",
) -> None:
    """
    Write an HRBP-confirmed fraud/legitimate label into dbo.P2P_FraudScores.

    Called in real-time when an HRBP reviews a PendingHRBPReview nomination:
      • is_fraud=True  (HRBP rejected)  → FraudScore=100, RiskLevel='CRITICAL',
                                          IsFraud=1, ConfirmedBy=confirmed_by
      • is_fraud=False (HRBP approved)  → FraudScore=0,   RiskLevel='NONE',
                                          IsFraud=0, ConfirmedBy=confirmed_by

    The row is created if it doesn't exist yet (nomination skipped the ML
    pipeline because no model was available at submission time).

    These confirmed labels are picked up by load_data() in train_fraud_model.py
    during the next weekly retrain:
        WHERE RiskLevel IN ('HIGH', 'CRITICAL')  →  IsFraud = 1
    so CRITICAL rows written here feed directly into supervised RF training.
    """
    fraud_score = 100  if is_fraud else 0
    risk_level  = "CRITICAL" if is_fraud else "NONE"
    is_fraud_int = 1 if is_fraud else 0

    with get_db_context() as session:
        session.execute(
            text("""
                MERGE dbo.P2P_FraudScores AS target
                USING (SELECT :nomination_id AS NominationId) AS src
                ON target.NominationId = src.NominationId
                WHEN MATCHED THEN
                    UPDATE SET FraudScore   = :fraud_score,
                               RiskLevel    = :risk_level,
                               IsFraud      = :is_fraud,
                               ConfirmedBy  = :confirmed_by,
                               ConfirmedAt  = SYSUTCDATETIME()
                WHEN NOT MATCHED THEN
                    INSERT (NominationId, FraudScore, RiskLevel, IsFraud,
                            ConfirmedBy, ConfirmedAt)
                    VALUES (:nomination_id, :fraud_score, :risk_level, :is_fraud,
                            :confirmed_by, SYSUTCDATETIME());
            """),
            {
                "nomination_id": nomination_id,
                "fraud_score":   fraud_score,
                "risk_level":    risk_level,
                "is_fraud":      is_fraud_int,
                "confirmed_by":  confirmed_by,
            },
        )
        session.commit()


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


def get_review_rate(tenant_id: int, days: int = 180) -> dict:
    """
    Historical HRBP flag/review rate for a tenant over the last N days.

    A nomination enters the HRBP review queue when fraud detection records a row
    in dbo.HRBP_FraudFlags (unique per NominationId). The review rate is that
    flagged count divided by all nominations submitted in the window — i.e. the
    fraction of incoming volume that lands on an HRBP's desk.

    Returns {"totalNominations", "flaggedNominations", "reviewRate"}.
    reviewRate is 0.0 when there is no volume (callers should treat that as
    "unknown" and may substitute a default).
    """
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT
                    COUNT(*) AS TotalNominations,
                    SUM(CASE WHEN f.NominationId IS NOT NULL THEN 1 ELSE 0 END)
                        AS FlaggedNominations
                FROM Nominations n
                JOIN Users u ON n.NominatorId = u.UserId
                LEFT JOIN dbo.HRBP_FraudFlags f ON f.NominationId = n.NominationId
                WHERE n.NominationDate >= DATEADD(DAY, :neg_days, CAST(GETDATE() AS DATE))
                  AND u.TenantId = :tenant_id
            """),
            {"neg_days": -abs(days), "tenant_id": tenant_id},
        ).fetchone()
        total   = (row[0] or 0) if row else 0
        flagged = (row[1] or 0) if row else 0
        return {
            "totalNominations":   total,
            "flaggedNominations": flagged,
            "reviewRate":         (flagged / total) if total > 0 else 0.0,
        }


def get_latest_forecast_run(tenant_id: int) -> dict | None:
    """
    Most recent completed forecast run for a tenant (written by the weekly
    forecast_models job stage). Returns run metadata incl. the model-comparison
    Metrics JSON, or None if the job has not produced a run yet.
    """
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT TOP (1)
                    RunId, TenantId, GeneratedAt, HorizonWeeks,
                    HistoryStart, HistoryEnd, Confidence, Metrics
                FROM dbo.ForecastRuns
                WHERE TenantId = :tenant_id AND Status = 'complete'
                ORDER BY GeneratedAt DESC
            """),
            {"tenant_id": tenant_id},
        ).fetchone()
        if not row:
            return None
        import json as _json
        return {
            "runId":        row[0],
            "tenantId":     row[1],
            "generatedAt":  row[2].isoformat() if row[2] else None,
            "horizonWeeks": row[3],
            "historyStart": row[4].isoformat() if row[4] else None,
            "historyEnd":   row[5].isoformat() if row[5] else None,
            "confidence":   row[6],
            "metrics":      _json.loads(row[7]) if row[7] else {},
        }


def get_forecasts(run_id: str) -> list[dict]:
    """All forecast rows for a run, ordered for charting."""
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT Series, Level, DepartmentTitle, Grain, TargetDate,
                       Horizon, Model, PointForecast, Lower, Upper
                FROM dbo.Forecasts
                WHERE RunId = :run_id
                ORDER BY Series, Level, DepartmentTitle, Grain, Horizon
            """),
            {"run_id": run_id},
        ).fetchall()
        return [
            {
                "series":     r[0],
                "level":      r[1],
                "department": r[2],
                "grain":      r[3],
                "targetDate": r[4].isoformat() if r[4] else None,
                "horizon":    r[5],
                "model":      r[6],
                "point":      r[7],
                "lower":      r[8],
                "upper":      r[9],
            }
            for r in rows
        ]


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

def get_demo_tenant_id() -> Optional[int]:
    """Return the internal TenantId for the Demo tenant, or None if not found."""
    with get_db_context() as session:
        row = session.execute(
            text("SELECT TenantId FROM dbo.Tenants WHERE is_demo = 1"),
        ).fetchone()
        return row[0] if row else None


def get_demo_aad_tenant_id() -> Optional[str]:
    """Return the Azure AD tenant GUID for the Demo tenant."""
    with get_db_context() as session:
        row = session.execute(
            text("SELECT AzureAdTenantId FROM dbo.Tenants WHERE is_demo = 1"),
        ).fetchone()
        return row[0] if row else None


def get_demo_site_url() -> Optional[str]:
    """Return Site_URL for the demo tenant, or None if the column is NULL or no demo tenant exists."""
    with get_db_context() as session:
        row = session.execute(
            text("SELECT Site_URL FROM dbo.Tenants WHERE is_demo = 1"),
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
                "WHERE u.userEmail = :email AND t.is_demo = 1"
            ),
            {"email": email},
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
                    (userPrincipalName, userEmail, FirstName, LastName, Title, ManagerId, TenantId,
                     created_by, updated_by)
                OUTPUT INSERTED.UserId
                VALUES (:upn, :email, :first, :last, 'Demo User', NULL, :tid,
                        :audit_by, :audit_by)
            """),
            {
                "upn":   upn,
                "email": email,
                "first": first_name,
                "last":  last_name,
                "tid":   tenant_id,
                "audit_by": get_actor(),
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
                SET    Status = :status,
                       updated_at = SYSUTCDATETIME(),
                       updated_by = :audit_by
                WHERE  NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id, "status": status, "audit_by": get_actor()},
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
                    INSERT (UserId, Role, AssignedBy, created_by, updated_by)
                    VALUES (:uid, :role, :assigned_by, :audit_by, :audit_by);
            """),
            {"uid": user_id, "role": role, "assigned_by": assigned_by, "audit_by": get_actor()},
        )
        session.commit()
        return result.rowcount > 0


def revoke_user_role(user_id: int, role: str) -> bool:
    """Remove a role assignment. Returns True if a row was deleted.

    Stamps updated_by with the current actor *before* deleting, so the temporal
    history's final version attributes the revocation to a person (SOC 2) — a
    bare DELETE would otherwise leave the last grantor as the apparent actor.
    """
    with get_db_context() as session:
        session.execute(
            text("""
                UPDATE dbo.UserRoles
                SET    updated_at = SYSUTCDATETIME(), updated_by = :actor
                WHERE  UserId = :uid AND Role = :role
            """),
            {"actor": get_actor(), "uid": user_id, "role": role},
        )
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
            "nomination_id":     row[0],
            "tenant_id":         row[1],
            "amount":            row[2],
            "currency":          row[3],
            "description":       row[4],
            "nomination_date":   str(row[5]),
            "status":            row[6],
            "approver_id":       row[7],
            "nominator_name":    row[8],
            "nominator_email":   row[9],
            "beneficiary_name":  row[10],
            "beneficiary_email": row[11],
            "fraud_score":       row[12],
            "risk_level":        row[13],
            "warning_flags":     row[14],
            "nominator_id":      row[15],
            "beneficiary_id":    row[16],
        }


def get_payroll_provider_for_tenant(tenant_id: int) -> Optional[dict]:
    """
    Return the payroll provider info for a tenant, or None if not configured.

    Returns {display_name, api_base_url, name} — enough for the frontend to
    show "Look Up Employee Pay (Gusto — api.gusto-demo.com)" in the heading.
    """
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT pp.display_name, pp.api_base_url, pp.name
                FROM   dbo.Tenants t
                INNER JOIN dbo.payroll_providers pp ON pp.id = t.payroll_provider_id
                WHERE  t.TenantId = :tenant_id
            """),
            {"tenant_id": tenant_id},
        ).fetchone()
    if not row:
        return None
    return {
        "display_name": row[0],
        "api_base_url":  row[1],
        "name":          row[2],
    }


# ===========================================================================
# NOMINATION LOG PERSISTENCE (SOC 2) — dbo.Nomination_Logs
# ===========================================================================

_NOMINATION_LOG_INSERT = text("""
    INSERT INTO dbo.Nomination_Logs
        (nomination_id, tenant_id, log_time, level, service, logger, message,
         message_id, details, exception, created_by, updated_by)
    VALUES
        (:nomination_id,
         COALESCE(:tenant_id, (SELECT TOP 1 u.TenantId FROM dbo.Nominations n
                               JOIN dbo.Users u ON u.UserId = n.NominatorId
                               WHERE n.NominationId = :nomination_id)),
         :log_time, :level, :service, :logger, :message,
         :message_id, :details, :exception, :created_by, :updated_by)
""")


def insert_nomination_logs(rows: list) -> None:
    """Bulk-insert nomination log rows. Called by the background log handler;
    created_at/updated_at are supplied by the DB default (SYSUTCDATETIME())."""
    if not rows:
        return
    with get_db_context() as session:
        session.execute(_NOMINATION_LOG_INSERT, rows)
        session.commit()


def get_nomination_logs(nomination_id: int) -> list:
    """Return persisted nomination log rows (log_time, level, service, logger, message)
    for the drawer, oldest first."""
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT log_time, level, service, logger, message, details
                FROM   dbo.Nomination_Logs
                WHERE  nomination_id = :nid
                ORDER  BY log_time ASC, log_id ASC
            """),
            {"nid": nomination_id},
        ).fetchall()
    return [tuple(r) for r in rows]


# ===========================================================================
# ADMIN SETUP — Organization (own-tenant settings; Tenants is temporal-audited)
# ===========================================================================

def get_organization_settings(tenant_id: int) -> dict:
    """Return editable org settings for a tenant (dedicated columns + Config JSON)."""
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT TenantName, Tagline, Company_Logo_URL, Site_URL,
                       fallback_admin_email, Domain, AzureAdTenantId, Config
                FROM   dbo.Tenants
                WHERE  TenantId = :tid
            """),
            {"tid": tenant_id},
        ).fetchone()
    if not row:
        return {}
    try:
        cfg = json.loads(row[7]) if row[7] else {}
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    theme = cfg.get("theme") if isinstance(cfg.get("theme"), dict) else {}
    return {
        "tenant_name":          row[0],
        "tagline":              row[1],
        "company_logo_url":     row[2],
        "site_url":             row[3],
        "fallback_admin_email": row[4],
        "domain":               row[5],   # read-only (routing/identity)
        "aad_tenant_id":        row[6],   # read-only (identity)
        "primary_color":        theme.get("primaryColor"),
        "locale":               cfg.get("locale"),
        "currency":             cfg.get("currency"),
        "min_award":            cfg.get("min_award"),
        "max_award":            cfg.get("max_award"),
    }


def update_organization_settings(tenant_id: int, data: dict, actor: str) -> None:
    """Update a tenant's own org settings. JSON fields are merged into Config."""
    with get_db_context() as session:
        row = session.execute(
            text("SELECT Config FROM dbo.Tenants WHERE TenantId = :tid"),
            {"tid": tenant_id},
        ).fetchone()
        try:
            cfg = json.loads(row[0]) if row and row[0] else {}
        except Exception:
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}

        if data.get("locale") is not None:
            cfg["locale"] = data["locale"]
        if data.get("currency") is not None:
            cfg["currency"] = data["currency"]
        if data.get("primary_color") is not None:
            theme = cfg.get("theme") if isinstance(cfg.get("theme"), dict) else {}
            theme["primaryColor"] = data["primary_color"]
            cfg["theme"] = theme
        if data.get("min_award") is not None:
            cfg["min_award"] = int(data["min_award"])
        if data.get("max_award") is not None:
            cfg["max_award"] = int(data["max_award"])

        session.execute(
            text("""
                UPDATE dbo.Tenants
                SET TenantName           = :name,
                    Tagline              = :tagline,
                    Company_Logo_URL     = :logo,
                    Site_URL             = :site,
                    fallback_admin_email = :fallback,
                    Config               = :config,
                    updated_at           = SYSUTCDATETIME(),
                    updated_by           = :actor
                WHERE TenantId = :tid
            """),
            {
                "name":     data.get("tenant_name"),
                "tagline":  data.get("tagline"),
                "logo":     data.get("company_logo_url"),
                "site":     data.get("site_url"),
                "fallback": data.get("fallback_admin_email"),
                "config":   json.dumps(cfg),
                "actor":    actor,
                "tid":      tenant_id,
            },
        )
        session.commit()


# ===========================================================================
# ADMIN SETUP — Roles & Access (own-tenant; UserRoles is temporal-audited)
# ===========================================================================

def user_in_tenant(user_id: int, tenant_id: int) -> bool:
    """True if the user belongs to the given tenant (own-tenant guard on writes)."""
    with get_db_context() as session:
        row = session.execute(
            text("SELECT 1 FROM dbo.Users WHERE UserId = :uid AND TenantId = :tid"),
            {"uid": user_id, "tid": tenant_id},
        ).fetchone()
    return row is not None


def get_tenant_role_members(tenant_id: int) -> list:
    """Users in the tenant who hold >=1 app role, grouped: {user_id, name, upn, roles[]}."""
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT u.UserId, u.FirstName, u.LastName, u.userPrincipalName, ur.Role
                FROM   dbo.UserRoles ur
                JOIN   dbo.Users u ON u.UserId = ur.UserId
                WHERE  u.TenantId = :tid
                ORDER  BY u.LastName, u.FirstName
            """),
            {"tid": tenant_id},
        ).fetchall()
    members: dict = {}
    for uid, first, last, upn, role in rows:
        name = f"{first or ''} {last or ''}".strip() or upn
        m = members.setdefault(uid, {"user_id": uid, "name": name, "upn": upn, "roles": []})
        m["roles"].append(role)
    return list(members.values())


def get_tenant_users_brief(tenant_id: int) -> list:
    """All users in the tenant (id, name, upn) for the role-assignment picker."""
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT UserId, FirstName, LastName, userPrincipalName
                FROM   dbo.Users
                WHERE  TenantId = :tid
                ORDER  BY LastName, FirstName
            """),
            {"tid": tenant_id},
        ).fetchall()
    return [
        {"user_id": r[0], "name": f"{r[1] or ''} {r[2] or ''}".strip() or r[3], "upn": r[3]}
        for r in rows
    ]


# ===========================================================================
# ADMIN SETUP — Award Categories (own-tenant; soft-delete via is_active)
# ===========================================================================

def get_categories_admin(tenant_id: int) -> list:
    """All categories for a tenant (active + inactive) for the admin UI."""
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT id, category_description, min_amount, max_amount, is_active
                FROM   dbo.nomination_categories
                WHERE  tenant_id = :tid
                ORDER  BY is_active DESC, category_description
            """),
            {"tid": tenant_id},
        ).fetchall()
    return [
        {"id": r[0], "category_description": r[1], "min_amount": r[2],
         "max_amount": r[3], "is_active": bool(r[4])}
        for r in rows
    ]


def category_in_tenant(category_id: int, tenant_id: int) -> bool:
    with get_db_context() as session:
        row = session.execute(
            text("SELECT 1 FROM dbo.nomination_categories WHERE id = :cid AND tenant_id = :tid"),
            {"cid": category_id, "tid": tenant_id},
        ).fetchone()
    return row is not None


def create_category(tenant_id: int, description: str, min_amount, max_amount,
                    is_active: bool, actor: str) -> int:
    with get_db_context() as session:
        row = session.execute(
            text("""
                INSERT INTO dbo.nomination_categories
                    (tenant_id, category_description, min_amount, max_amount,
                     is_active, created_by, updated_by)
                OUTPUT INSERTED.id
                VALUES (:tid, :desc, :min, :max, :active, :actor, :actor)
            """),
            {"tid": tenant_id, "desc": description, "min": min_amount, "max": max_amount,
             "active": 1 if is_active else 0, "actor": actor},
        ).fetchone()
        session.commit()
        return row[0]


def update_category(category_id: int, tenant_id: int, description: str, min_amount,
                    max_amount, is_active: bool, actor: str) -> bool:
    with get_db_context() as session:
        result = session.execute(
            text("""
                UPDATE dbo.nomination_categories
                SET category_description = :desc,
                    min_amount           = :min,
                    max_amount           = :max,
                    is_active            = :active,
                    updated_at           = SYSUTCDATETIME(),
                    updated_by           = :actor
                WHERE id = :cid AND tenant_id = :tid
            """),
            {"desc": description, "min": min_amount, "max": max_amount,
             "active": 1 if is_active else 0, "actor": actor,
             "cid": category_id, "tid": tenant_id},
        )
        session.commit()
        return result.rowcount > 0


# ===========================================================================
# ADMIN SETUP — Fraud / Integrity (Tenants.desc_check_config + integrity_config)
# ===========================================================================

def get_fraud_settings(tenant_id: int) -> dict:
    """Return the tenant's description-quality + fraud-routing config, flattened,
    with system defaults filled in for any missing key."""
    with get_db_context() as session:
        row = session.execute(
            text("SELECT desc_check_config, integrity_config FROM dbo.Tenants WHERE TenantId = :tid"),
            {"tid": tenant_id},
        ).fetchone()

    def _parse(v):
        try:
            d = json.loads(v) if v else {}
        except Exception:
            d = {}
        return d if isinstance(d, dict) else {}

    dcc = _parse(row[0]) if row else {}
    ic  = _parse(row[1]) if row else {}
    routing = ic.get("score_routing") if isinstance(ic.get("score_routing"), dict) else {}
    graph   = ic.get("graph_pattern") if isinstance(ic.get("graph_pattern"), dict) else {}
    phrases = dcc.get("boilerplate_phrases")
    return {
        # Fraud score routing (0..100 cutoffs)
        "low_threshold":                  int(routing.get("low_threshold", 20)),
        "medium_threshold":               int(routing.get("medium_threshold", 40)),
        "high_threshold":                 int(routing.get("high_threshold", 60)),
        "critical_threshold":             int(routing.get("critical_threshold", 80)),
        "detection_window_days":          int(graph.get("detection_window_days", 365)),
        # Description quality
        "use_char_count":                 bool(dcc.get("use_char_count", False)),
        "min_char_count":                 int(dcc.get("min_char_count", 12)),
        "min_word_count":                 int(dcc.get("min_word_count", 3)),
        "category_alignment_threshold":   float(dcc.get("category_alignment_threshold", 0.15)),
        "duplicate_similarity_threshold": float(dcc.get("duplicate_similarity_threshold", 0.85)),
        "llm_category_check_enabled":     bool(dcc.get("llm_category_check_enabled", False)),
        "llm_fit_threshold":              float(dcc.get("llm_fit_threshold", 0.40)),
        "llm_instructions":               dcc.get("llm_instructions"),
        "boilerplate_phrases":            phrases if isinstance(phrases, list) else [],
    }


def update_fraud_settings(tenant_id: int, data: dict, actor: str) -> None:
    """Merge the flattened config back into desc_check_config + integrity_config,
    preserving any unrelated keys already present."""
    with get_db_context() as session:
        row = session.execute(
            text("SELECT desc_check_config, integrity_config FROM dbo.Tenants WHERE TenantId = :tid"),
            {"tid": tenant_id},
        ).fetchone()

        def _parse(v):
            try:
                d = json.loads(v) if v else {}
            except Exception:
                d = {}
            return d if isinstance(d, dict) else {}

        dcc = _parse(row[0]) if row else {}
        ic  = _parse(row[1]) if row else {}

        dcc["use_char_count"]                 = bool(data["use_char_count"])
        dcc["min_char_count"]                 = int(data["min_char_count"])
        dcc["min_word_count"]                 = int(data["min_word_count"])
        dcc["category_alignment_threshold"]   = float(data["category_alignment_threshold"])
        dcc["duplicate_similarity_threshold"] = float(data["duplicate_similarity_threshold"])
        dcc["llm_category_check_enabled"]     = bool(data["llm_category_check_enabled"])
        dcc["llm_fit_threshold"]              = float(data["llm_fit_threshold"])
        dcc["llm_instructions"]               = data.get("llm_instructions") or None
        dcc["boilerplate_phrases"]            = data.get("boilerplate_phrases", [])

        routing = ic.get("score_routing") if isinstance(ic.get("score_routing"), dict) else {}
        routing["low_threshold"]      = int(data["low_threshold"])
        routing["medium_threshold"]   = int(data["medium_threshold"])
        routing["high_threshold"]     = int(data["high_threshold"])
        routing["critical_threshold"] = int(data["critical_threshold"])
        ic["score_routing"] = routing
        graph = ic.get("graph_pattern") if isinstance(ic.get("graph_pattern"), dict) else {}
        graph["detection_window_days"] = int(data["detection_window_days"])
        ic["graph_pattern"] = graph

        session.execute(
            text("""
                UPDATE dbo.Tenants
                SET desc_check_config = :dcc,
                    integrity_config  = :ic,
                    updated_at        = SYSUTCDATETIME(),
                    updated_by        = :actor
                WHERE TenantId = :tid
            """),
            {"dcc": json.dumps(dcc), "ic": json.dumps(ic), "actor": actor, "tid": tenant_id},
        )
        session.commit()


# ===========================================================================
# ADMIN SETUP — Payroll Integration (own-tenant; provider + token status)
# ===========================================================================

def get_payroll_setup(tenant_id: int) -> dict:
    """Return the tenant's configured payroll provider and its connection status.

    provider is None when Tenants.payroll_provider_id is unset. `connected`
    reflects whether an OAuth token row exists for that provider."""
    with get_db_context() as session:
        prov = session.execute(
            text("""
                SELECT pp.id, pp.name, pp.display_name, pp.company_id_at_provider, pp.api_base_url
                FROM   dbo.Tenants t
                JOIN   dbo.payroll_providers pp ON pp.id = t.payroll_provider_id
                WHERE  t.TenantId = :tid
            """),
            {"tid": tenant_id},
        ).fetchone()
        if not prov:
            return {"provider": None, "connected": False, "token_expires_at": None}
        tok = session.execute(
            text("SELECT token_expires_at FROM dbo.payroll_tokens WHERE provider_id = :pid"),
            {"pid": prov[0]},
        ).fetchone()
    return {
        "provider": {
            "id": prov[0], "name": prov[1], "display_name": prov[2],
            "company_id_at_provider": prov[3], "api_base_url": prov[4],
        },
        "connected": tok is not None,
        "token_expires_at": str(tok[0]) if tok and tok[0] else None,
    }


def disconnect_payroll(tenant_id: int, actor: str) -> bool:
    """Delete the tenant's payroll OAuth token (revoke the local connection).

    Stamps updated_by before deleting so the temporal history (payroll_tokens is
    system-versioned) attributes the disconnect to a person."""
    with get_db_context() as session:
        prov = session.execute(
            text("SELECT payroll_provider_id FROM dbo.Tenants WHERE TenantId = :tid"),
            {"tid": tenant_id},
        ).fetchone()
        if not prov or prov[0] is None:
            return False
        pid = prov[0]
        session.execute(
            text("UPDATE dbo.payroll_tokens SET updated_at = SYSUTCDATETIME(), updated_by = :actor WHERE provider_id = :pid"),
            {"actor": actor, "pid": pid},
        )
        result = session.execute(
            text("DELETE FROM dbo.payroll_tokens WHERE provider_id = :pid"),
            {"pid": pid},
        )
        session.commit()
        return result.rowcount > 0


# ===========================================================================
# AUDIT & ACCESS REVIEW QUERIES  (Setup > Audit & Access Review, read-only)
# All tenant-scoped; timestamps returned as UTC ISO strings (SYSUTCDATETIME /
# Azure SQL GETDATE() are UTC) so the browser can localise them.
# ===========================================================================

# SQL Server's row-end sentinel for an open (currently-active) temporal row.
_TEMPORAL_OPEN_YEAR = 9999


def _iso_utc(dt) -> Optional[str]:
    """Render a naive UTC datetime as an ISO-8601 string with a 'Z' marker."""
    if dt is None:
        return None
    s = dt.isoformat()
    return s if s.endswith("Z") or "+" in s else s + "Z"


def get_access_review(tenant_id: int) -> List[dict]:
    """Current app-role assignments for the tenant (the 'who has access' snapshot).

    App-managed roles only (HRBP / PayrollBP / Support). AWard_Nomination_Admin is
    Entra-managed and never lands in dbo.UserRoles, so it is intentionally absent.
    """
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT u.UserId, u.userPrincipalName, u.FirstName, u.LastName, u.Title,
                       ur.Role, ur.created_by, ur.created_at, ur.updated_by, ur.updated_at
                FROM   dbo.UserRoles ur
                JOIN   dbo.Users u ON u.UserId = ur.UserId
                WHERE  ur.TenantId = :tid
                ORDER  BY u.LastName, u.FirstName, ur.Role
            """),
            {"tid": tenant_id},
        ).fetchall()
    out = []
    for uid, upn, first, last, title, role, cby, cat, uby, uat in rows:
        out.append({
            "user_id":      uid,
            "upn":          upn,
            "name":         f"{first or ''} {last or ''}".strip() or upn,
            "title":        title,
            "role":         role,
            "granted_by":   cby,
            "granted_at":   _iso_utc(cat),
            "updated_by":   uby,
            "updated_at":   _iso_utc(uat),
        })
    return out


def get_role_change_history(tenant_id: int, limit: int = 200) -> List[dict]:
    """Full grant/revoke timeline for the tenant from UserRoles temporal history.

    Reads every row version (current + history). An open ValidTo (year 9999)
    means the assignment is still active; a finite ValidTo means it ended
    (revoked or superseded) at that instant. ValidFrom/ValidTo are HIDDEN period
    columns — returned only because they are named explicitly here.
    """
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT TOP (:limit)
                       ur.UserId, u.userPrincipalName, u.FirstName, u.LastName,
                       ur.Role, ur.created_by, ur.updated_by,
                       ur.ValidFrom, ur.ValidTo
                FROM   dbo.UserRoles FOR SYSTEM_TIME ALL ur
                LEFT   JOIN dbo.Users u ON u.UserId = ur.UserId
                WHERE  ur.TenantId = :tid
                ORDER  BY ur.ValidFrom DESC
            """),
            {"tid": tenant_id, "limit": limit},
        ).fetchall()
    out = []
    for uid, upn, first, last, role, cby, uby, vfrom, vto in rows:
        active = vto is not None and vto.year >= _TEMPORAL_OPEN_YEAR
        out.append({
            "user_id":     uid,
            "upn":         upn,
            "name":        (f"{first or ''} {last or ''}".strip() or upn) if upn else f"user #{uid}",
            "role":        role,
            "created_by":  cby,
            "updated_by":  uby,
            "valid_from":  _iso_utc(vfrom),
            "valid_to":    None if active else _iso_utc(vto),
            "active":      active,
        })
    return out


def get_impersonation_audit(tenant_id: int, limit: int = 200) -> List[dict]:
    """Impersonation audit trail, scoped to admins belonging to this tenant.

    Impersonation_AuditLog stores UPNs, not TenantId, so we scope by requiring
    the acting admin (AdminUPN) to be a user in this tenant. Impersonation is
    already tenant-bound at the point of use, so this cannot leak other tenants.
    """
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT TOP (:limit)
                       l.Timestamp, l.AdminUPN, l.ImpersonatedUPN,
                       l.Action, l.Details, l.IpAddress
                FROM   dbo.Impersonation_AuditLog l
                WHERE  EXISTS (
                    SELECT 1 FROM dbo.Users u
                    WHERE u.userPrincipalName = l.AdminUPN AND u.TenantId = :tid
                )
                ORDER  BY l.Timestamp DESC
            """),
            {"tid": tenant_id, "limit": limit},
        ).fetchall()
    out = []
    for ts, admin_upn, imp_upn, action, details, ip in rows:
        out.append({
            "time":             _iso_utc(ts),
            "admin_upn":        admin_upn,
            "impersonated_upn": imp_upn,
            "action":           action,
            "details":          details,
            "ip_address":       ip,
        })
    return out
