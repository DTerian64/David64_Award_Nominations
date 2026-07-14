"""
sqlhelper.py – Payroll Broker database helper
==============================================
Runtime queries against the payroll tables and shared Nominations/Tenants
tables.  Migrations are managed by the backend's Alembic (0030_payroll_tables.py).

Schema (managed by backend/alembic/versions/0030_payroll_tables.py):
  payroll_providers  — one row per configured provider instance.
                       payroll_providers.name is the type discriminator.
                       company_id_at_provider is set during OAuth callback.
  payroll_tokens     — rotating OAuth credentials, keyed by provider_id (UNIQUE).
  payroll_submissions— one row per submission, bridges provider_payroll_ref
                       back to nomination_id for the webhook handler.

  Tenants.payroll_provider_id — FK → payroll_providers.id

Authentication:
  Always Entra via the container's Managed Identity (utils/azure_credential.py).
  No SQL username/password.
"""

import logging
import os
import struct
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, Tuple
from urllib.parse import quote_plus

from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, LargeBinary, String, Unicode,
    UniqueConstraint, create_engine, event, text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DB_SERVER   = os.getenv("SQL_SERVER")
DB_NAME     = os.getenv("SQL_DATABASE")
DB_DRIVER   = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")


# ===========================================================================
# ORM Models
# ===========================================================================

class Base(DeclarativeBase):
    pass


class PayrollProviderORM(Base):
    """
    dbo.payroll_providers — one row per configured payroll provider instance.

    The payroll broker reads company_id_at_provider (e.g. Gusto company UUID)
    and provider_config from here; it writes company_id_at_provider during
    the OAuth callback once the company is known.
    """
    __tablename__ = "payroll_providers"

    id                      = Column(Integer, primary_key=True, autoincrement=True)
    name                    = Column(String(50),    nullable=False)   # "gusto", "workday"
    display_name            = Column(String(100),   nullable=False)
    company_id_at_provider  = Column(String(100),   nullable=True)    # set at OAuth callback
    provider_config         = Column(Unicode(None), nullable=True)    # NVARCHAR(MAX) JSON
    api_base_url            = Column(String(255),   nullable=True)    # NULL = use hardcoded default
    oauth_base_url          = Column(String(255),   nullable=True)
    created_at              = Column(DateTime, server_default=text("GETUTCDATE()"))
    created_by              = Column(Unicode(256), nullable=True)
    updated_at              = Column(DateTime, server_default=text("GETUTCDATE()"),
                                     onupdate=datetime.utcnow)
    updated_by              = Column(Unicode(256), nullable=True)


class PayrollTokenORM(Base):
    """
    dbo.payroll_tokens — rotating OAuth credentials for one provider instance.

    UNIQUE on provider_id — one token row per provider row.
    No tenant_id here: the tenant→provider link lives in Tenants.payroll_provider_id.
    """
    __tablename__ = "payroll_tokens"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    provider_id      = Column(Integer, ForeignKey("payroll_providers.id"), nullable=False)
    access_token     = Column(LargeBinary, nullable=False)   # AES-256-GCM ciphertext
    refresh_token    = Column(LargeBinary, nullable=False)   # AES-256-GCM ciphertext
    token_expires_at = Column(DateTime, nullable=True)
    created_at       = Column(DateTime, server_default=text("GETUTCDATE()"))
    updated_at       = Column(DateTime, server_default=text("GETUTCDATE()"),
                              onupdate=datetime.utcnow)
    created_by       = Column(Unicode(256), nullable=True)
    updated_by       = Column(Unicode(256), nullable=True)

    __table_args__ = (
        UniqueConstraint("provider_id", name="uq_payroll_tokens_provider"),
    )


class PayrollSubmissionORM(Base):
    """
    dbo.payroll_submissions — one row per payroll submission attempt.

    Bridges the provider's external reference (e.g. Gusto payroll UUID) back
    to nomination_id so the webhook handler can resolve the nomination.

    Status lifecycle:
      submitted — upserted before calling the provider
      rejected  — provider returned an error; reason = provider error message
      accepted  — provider accepted the payroll; completed_at = stamped
    """
    __tablename__ = "payroll_submissions"

    id                   = Column(Integer,    primary_key=True, autoincrement=True)
    nomination_id        = Column(Integer,    nullable=False)   # FK to dbo.Nominations enforced at DB level; omitted here because Nominations is owned by the backend service
    provider_id          = Column(Integer,    ForeignKey("payroll_providers.id"), nullable=False)
    provider_payroll_ref = Column(String(100), nullable=True)
    status               = Column(String(50),  nullable=False, default="submitted")
    reason               = Column(String(1000), nullable=True)
    submitted_at         = Column(DateTime,   server_default=text("GETUTCDATE()"))
    completed_at         = Column(DateTime,   nullable=True)
    created_at           = Column(DateTime,   server_default=text("GETUTCDATE()"))
    created_by           = Column(Unicode(256), nullable=True)
    updated_at           = Column(DateTime,   server_default=text("GETUTCDATE()"),
                                  onupdate=datetime.utcnow)
    updated_by           = Column(Unicode(256), nullable=True)


# ===========================================================================
# Audit stamping (SOC 2) - created_by / updated_by
# ===========================================================================
_AUDIT_ACTOR = "svc:payroll-broker"


@event.listens_for(Session, "before_flush")
def _stamp_audit_actor(session, flush_context, instances):
    """Stamp the service actor on any audit-bearing model at flush time.

    payroll-broker writes autonomously (OAuth callback, token refresh, worker),
    so there is no human actor; created_by/updated_by carry a constant service
    marker. Covers both branches of the upsert helpers without per-site edits.
    """
    for obj in session.new:
        if hasattr(obj, "updated_by"):
            if getattr(obj, "created_by", None) is None:
                obj.created_by = _AUDIT_ACTOR
            obj.updated_by = _AUDIT_ACTOR
    for obj in session.dirty:
        if hasattr(obj, "updated_by"):
            obj.updated_by = _AUDIT_ACTOR


# ===========================================================================
# Engine factory
# ===========================================================================

def _build_engine():
    """
    SQLAlchemy engine using an Entra token via DefaultAzureCredential -- the
    container's Managed Identity in Azure (selected by MI_CLIENT_ID), or the
    developer's az / VS Code login locally. NullPool: tokens expire, so a fresh
    one is fetched per connection.
    """
    from .azure_credential import credential

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

engine       = _build_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


# ===========================================================================
# Session helpers
# ===========================================================================

@contextmanager
def get_db_context():
    """Context manager: yields a Session and handles rollback/close."""
    session: Session = SessionLocal()
    try:
        yield session
    except Exception as e:
        session.rollback()
        logger.error("Error in database context: %s", e)
        raise
    finally:
        session.close()


# ===========================================================================
# PAYROLL PROVIDER QUERIES
# ===========================================================================

def get_provider_for_tenant(tenant_id: int) -> Optional[PayrollProviderORM]:
    """
    Return the PayrollProviderORM for the tenant via Tenants.payroll_provider_id.

    Returns None if the tenant has no payroll provider configured.
    Uses raw SQL to read Tenants (backend owns the ORM model for that table).
    """
    with get_db_context() as session:
        row = session.execute(
            text("SELECT payroll_provider_id FROM dbo.Tenants WHERE TenantId = :tid"),
            {"tid": tenant_id},
        ).fetchone()

        if not row or row[0] is None:
            return None

        provider_id = int(row[0])
        return (
            session.query(PayrollProviderORM)
            .filter_by(id=provider_id)
            .first()
        )


def update_provider_company_ref(provider_id: int, company_id_at_provider: str) -> None:
    """
    Persist the company's external identifier on the provider row.

    Called by the OAuth callback once /v1/me returns the company UUID.
    Safe to call repeatedly (idempotent update).
    """
    with get_db_context() as session:
        provider = session.query(PayrollProviderORM).filter_by(id=provider_id).first()
        if provider:
            provider.company_id_at_provider = company_id_at_provider
            session.commit()
    logger.info(
        "Updated payroll_providers id=%d company_id_at_provider=%s",
        provider_id, company_id_at_provider,
    )


# ===========================================================================
# PAYROLL TOKEN QUERIES
# ===========================================================================

def get_payroll_token_by_provider_id(provider_id: int) -> Optional[PayrollTokenORM]:
    """
    Return the PayrollTokenORM for the given provider, or None if OAuth has
    not been completed yet for this provider.
    """
    with get_db_context() as session:
        return (
            session.query(PayrollTokenORM)
            .filter_by(provider_id=provider_id)
            .first()
        )


def upsert_payroll_token(
    provider_id:      int,
    access_token:     bytes,
    refresh_token:    bytes,
    token_expires_at: Optional[datetime],
) -> None:
    """
    Insert or update the OAuth token for the given provider.

    access_token and refresh_token must be AES-256-GCM ciphertext produced
    by utils.crypto.encrypt() — never pass plaintext strings here.

    Called by:
      • gusto_oauth_router.py — after initial OAuth code exchange
      • provider.py           — after each token refresh
    """
    with get_db_context() as session:
        existing = (
            session.query(PayrollTokenORM)
            .filter_by(provider_id=provider_id)
            .first()
        )
        if existing:
            existing.access_token     = access_token
            existing.refresh_token    = refresh_token
            existing.token_expires_at = token_expires_at
            existing.updated_at       = datetime.utcnow()
        else:
            session.add(PayrollTokenORM(
                provider_id=provider_id,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_at=token_expires_at,
            ))
        session.commit()
    logger.info("Payroll token upserted provider_id=%d", provider_id)


# ===========================================================================
# PAYROLL SUBMISSION QUERIES
# ===========================================================================

def upsert_payroll_submission(
    nomination_id:        int,
    provider_id:          int,
    status:               str,
    provider_payroll_ref: Optional[str] = None,
    reason:               Optional[str] = None,
    completed_at:         Optional[datetime] = None,
) -> int:
    """
    Insert or update a payroll submission row keyed on nomination_id.

    Call three times during the worker lifecycle:
      1. Before calling the provider   — status='submitted'
      2. On provider rejection         — status='rejected',  reason=<error msg>
      3. On provider acceptance        — status='accepted',  completed_at=utcnow()

    Returns the local submission id.
    """
    with get_db_context() as session:
        sub = (
            session.query(PayrollSubmissionORM)
            .filter_by(nomination_id=nomination_id)
            .first()
        )
        if sub is None:
            sub = PayrollSubmissionORM(
                nomination_id=nomination_id,
                provider_id=provider_id,
            )
            session.add(sub)

        sub.status = status
        sub.reason = reason          # always written — clears stale failure msg on re-acceptance
        if provider_payroll_ref is not None:
            sub.provider_payroll_ref = provider_payroll_ref
        if completed_at is not None:
            sub.completed_at = completed_at

        session.commit()
        session.refresh(sub)
        return sub.id


def get_submission_by_payroll_ref(
    provider_payroll_ref: str,
) -> Optional[Tuple[int, str]]:
    """
    Look up a submission by the external payroll reference.

    Returns (nomination_id, status) or None if not found.
    Used by the webhook handler to resolve the nomination from provider callback.
    """
    with get_db_context() as session:
        sub = (
            session.query(PayrollSubmissionORM)
            .filter_by(provider_payroll_ref=provider_payroll_ref)
            .first()
        )
        if sub:
            return sub.nomination_id, sub.status
        return None


def update_submission_status(
    provider_payroll_ref: str,
    status:               str,
) -> bool:
    """
    Update the status of a payroll submission ('completed' / 'failed').

    Returns True if a row was updated.
    """
    with get_db_context() as session:
        sub = (
            session.query(PayrollSubmissionORM)
            .filter_by(provider_payroll_ref=provider_payroll_ref)
            .first()
        )
        if not sub:
            return False
        sub.status = status
        if status in ("completed", "failed"):
            sub.completed_at = datetime.utcnow()
        session.commit()
        return True


# ===========================================================================
# READ-ONLY QUERIES AGAINST SHARED TABLES (raw SQL — backend owns ORM models)
# ===========================================================================

def get_nomination_for_payroll(nomination_id: int) -> Optional[dict]:
    """
    Fetch the fields the worker needs to submit payroll for an approved
    nomination: amount, currency, beneficiary UPN, and tenant_id.

    userPrincipalName is used as the lookup key against the payroll provider
    (e.g. Gusto find_employee_by_email) because it is the canonical, stable
    identity for each employee — guaranteed to match what was registered in
    the provider during onboarding.

    Returns None if the nomination does not exist.
    """
    with get_db_context() as session:
        row = session.execute(
            text("""
                SELECT
                    n.NominationId,
                    n.Amount,
                    n.Currency,
                    beneficiary.userPrincipalName  AS BeneficiaryUPN,
                    beneficiary.FirstName          AS BeneficiaryFirstName,
                    beneficiary.LastName           AS BeneficiaryLastName,
                    beneficiary.TenantId           AS TenantId
                FROM dbo.Nominations n
                INNER JOIN dbo.Users beneficiary ON n.BeneficiaryId = beneficiary.UserId
                WHERE n.NominationId = :nomination_id
            """),
            {"nomination_id": nomination_id},
        ).fetchone()

    if not row:
        return None
    return {
        "nomination_id":              int(row[0]),
        "amount":                     float(row[1]),
        "currency":                   row[2],
        "beneficiary_upn":            row[3],
        "beneficiary_first":          row[4],
        "beneficiary_last":           row[5],
        "tenant_id":                  int(row[6]),
    }


def get_payroll_refs_for_employee_month(
    upn:         str,
    provider_id: int,
    year:        int,
    month:       int,
) -> list[str]:
    """
    Return accepted provider_payroll_refs for a specific employee + calendar month.

    Joins payroll_submissions → Nominations → Users (by BeneficiaryId), filtered
    by the beneficiary's userPrincipalName, provider_id, status='accepted', and
    the year/month of completed_at.

    Used to supplement the Gusto list endpoint, which silently omits off-cycle
    payrolls that are unprocessed or auto-voided in sandbox.

    Returns a (possibly empty) list of provider_payroll_ref strings.
    """
    with get_db_context() as session:
        rows = session.execute(
            text("""
                SELECT  ps.provider_payroll_ref
                FROM    dbo.payroll_submissions ps
                INNER JOIN dbo.Nominations n ON ps.nomination_id = n.NominationId
                INNER JOIN dbo.Users       u ON n.BeneficiaryId  = u.UserId
                WHERE   u.userPrincipalName    = :upn
                  AND   ps.provider_id         = :provider_id
                  AND   ps.status              = 'accepted'
                  AND   ps.provider_payroll_ref IS NOT NULL
                  AND   YEAR (ps.completed_at) = :year
                  AND   MONTH(ps.completed_at) = :month
            """),
            {"upn": upn, "provider_id": provider_id, "year": year, "month": month},
        ).fetchall()
    refs = [row[0] for row in rows if row[0]]
    logger.info(
        "get_payroll_refs_for_employee_month upn=%s provider_id=%d year=%d month=%d refs=%s",
        upn, provider_id, year, month, refs,
    )
    return refs


def get_tenant_name(tenant_id: int) -> Optional[str]:
    """Return TenantName for a given tenant_id."""
    with get_db_context() as session:
        row = session.execute(
            text("SELECT TenantName FROM dbo.Tenants WHERE TenantId = :tid"),
            {"tid": tenant_id},
        ).fetchone()
        return row[0] if row else None


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
    """Bulk-insert nomination log rows (called by the background log handler)."""
    if not rows:
        return
    with get_db_context() as session:
        session.execute(_NOMINATION_LOG_INSERT, rows)
        session.commit()
