"""
demo_router.py
==============
Public self-registration endpoint for demo-awards.terian-services.com.

POST /api/demo/join
-------------------
No authentication required.

Request body:
    first_name  str   (1–50 chars)
    last_name   str   (1–50 chars)
    email       str   (valid email, stored as userEmail)
    is_admin    bool  (if true, assigns AWard_Nomination_Admin role)

Response:
    {
        "upn":           str,   # the new @demo.terian-services.com account
        "temp_password": str,   # one-time password — shown once, not stored
        "aad_tenant_id": str,   # Demo tenant GUID (for MSAL authority override)
        "user_id":       int,   # internal dbo.Users UserId
    }

Errors:
    422 — validation failure
    429 — rate limit exceeded (max 5 registrations per IP per hour)
    503 — Graph API unavailable / demo tenant not configured

Rate limiting is in-memory and resets on process restart.  For production
scale, replace with a Redis-backed counter.
"""

import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, validator

import sqlhelper2 as sqlhelper
import graph_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/demo", tags=["Demo"])

# ---------------------------------------------------------------------------
# Rate limiting  (in-memory, per-IP)
# ---------------------------------------------------------------------------

_RATE_LIMIT_MAX  = 5          # registrations per window
_RATE_LIMIT_MINS = 60         # window length in minutes
_rate_log: dict[str, list[datetime]] = defaultdict(list)


def _check_rate_limit(ip: str) -> None:
    """Raise 429 if the IP has exceeded the registration rate limit."""
    now    = datetime.utcnow()
    cutoff = now - timedelta(minutes=_RATE_LIMIT_MINS)

    # Prune old timestamps
    _rate_log[ip] = [t for t in _rate_log[ip] if t > cutoff]

    if len(_rate_log[ip]) >= _RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many registrations from this IP. "
                f"Maximum {_RATE_LIMIT_MAX} per hour."
            ),
        )

    _rate_log[ip].append(now)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class DemoJoinRequest(BaseModel):
    first_name: str
    last_name:  str
    email:      str
    is_admin:   bool = False

    @validator("first_name", "last_name")
    def _name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        if len(v) > 50:
            raise ValueError("Name must be 50 characters or fewer")
        return v

    @validator("email")
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        if len(v) > 256:
            raise ValueError("Email too long")
        return v


class DemoJoinResponse(BaseModel):
    upn:           str
    temp_password: str
    aad_tenant_id: str
    user_id:       int


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/join",
    response_model=DemoJoinResponse,
    summary="Self-register a demo user",
    description=(
        "Creates an AAD account in the Demo tenant, adds the user to dbo.Users, "
        "and (if is_admin=true) assigns the AWard_Nomination_Admin role. "
        "No authentication required. Rate-limited to 5 registrations per IP per hour."
    ),
)
async def demo_join(body: DemoJoinRequest, request: Request) -> DemoJoinResponse:
    # ── Rate limit ────────────────────────────────────────────────────────────
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    # ── Resolve demo tenant ───────────────────────────────────────────────────
    tenant_id = sqlhelper.get_demo_tenant_id()
    if tenant_id is None:
        logger.error("Demo tenant not found in dbo.Tenants — seed_demo.py may not have run.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo environment is not configured. Please contact the administrator.",
        )

    aad_tenant_id = sqlhelper.get_demo_aad_tenant_id()
    if not aad_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo AAD tenant ID not found.",
        )

    # ── Create AAD account ────────────────────────────────────────────────────
    try:
        aad_result = graph_admin.create_aad_user(body.first_name, body.last_name)
    except RuntimeError as e:
        logger.error("AAD user creation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not create demo account: {e}",
        )

    upn          = aad_result["upn"]
    oid          = aad_result["oid"]
    temp_password = aad_result["temp_password"]

    # ── Assign admin role (if requested) ──────────────────────────────────────
    if body.is_admin:
        try:
            graph_admin.assign_admin_role(oid)
        except RuntimeError as e:
            # Non-fatal for DB step — log and continue; admin role can be
            # assigned manually via assign_admin_role.py if Graph API is flaky.
            logger.error("Admin role assignment failed for oid=%s: %s", oid, e)

    # ── Create dbo.Users row ──────────────────────────────────────────────────
    # Check for duplicate (shouldn't happen since graph_admin generates unique UPNs,
    # but guard against retry/race conditions)
    if sqlhelper.upn_exists_in_tenant(upn, tenant_id):
        # UPN already in DB — look up the existing user and return it
        # (the caller can still trigger MSAL login with the same UPN)
        logger.warning("UPN %s already in dbo.Users for tenant %d — skipping insert.", upn, tenant_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A user with this name already exists ({upn}). Please try a different name.",
        )

    try:
        user_id = sqlhelper.create_demo_user(
            first_name=body.first_name,
            last_name=body.last_name,
            email=body.email,
            upn=upn,
            tenant_id=tenant_id,
        )
    except Exception as e:
        logger.error("DB insert failed for %s: %s", upn, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User account was created in Azure AD but could not be saved to the database.",
        )

    logger.info(
        "Demo self-registration complete: upn=%s user_id=%d is_admin=%s ip=%s",
        upn, user_id, body.is_admin, client_ip,
    )

    return DemoJoinResponse(
        upn=upn,
        temp_password=temp_password,
        aad_tenant_id=aad_tenant_id,
        user_id=user_id,
    )
