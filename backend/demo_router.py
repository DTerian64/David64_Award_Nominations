"""
demo_router.py
==============
Public self-registration endpoint for demo-awards.terian-services.com.

POST /api/demo/request
----------------------
No authentication required.

Flow:
  1. Visitor submits First Name, Last Name, Email, Is Admin?
  2. Backend calls Graph POST /invitations (sendInvitationMessage=False)
  3. Backend sends its own branded invitation email via SMTP (email_utils)
  4. If Is Admin: assigns AWard_Nomination_Admin role to the new guest object
  5. Creates a dbo.Users row (UPN = email) so auth.py can resolve them on first sign-in
  6. Logs the request to dbo.DemoRegistrationRequests for audit / rate-limit

Rate limits (DB-backed, survive process restarts):
  • Max 3 invitations per IP per hour
  • Max 1 invitation per email per hour  (same email → "already invited" response)

The invite redirect URL is hardcoded to /demo/welcome so the visitor lands on
our branded welcome page after accepting the Microsoft invitation.
"""

import logging
import os
import re

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, validator

import sqlhelper2 as sqlhelper
import graph_admin
from service_bus_publisher import publish_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/demo", tags=["Demo"])

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_DEMO_ORIGIN = os.getenv("DEMO_ORIGIN", "https://demo-awards.terian-services.com")
_INVITE_REDIRECT_URL = f"{_DEMO_ORIGIN}/demo/welcome"

_RATE_LIMIT_PER_IP    = 3   # max invitations from one IP per hour
_RATE_LIMIT_PER_EMAIL = 1   # max invitations to one email per hour

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class DemoRequestBody(BaseModel):
    first_name: str
    last_name:  str
    email:      str
    is_admin:   bool = False

    @validator("first_name", "last_name")
    def _name_valid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        if len(v) > 50:
            raise ValueError("Name must be 50 characters or fewer")
        return v

    @validator("email")
    def _email_valid(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        if len(v) > 256:
            raise ValueError("Email too long")
        return v


class DemoRequestResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/request",
    response_model=DemoRequestResponse,
    summary="Request demo access",
    description=(
        "Sends a Microsoft B2B invitation to the provided email address. "
        "No authentication required. Rate-limited by IP and email."
    ),
)
async def demo_request(body: DemoRequestBody, request: Request) -> DemoRequestResponse:
    try:
        return await _demo_request_inner(body, request)
    except HTTPException:
        raise  # pass through clean HTTP errors unchanged
    except Exception as exc:
        logger.exception("Unhandled error in POST /api/demo/request: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {type(exc).__name__}: {exc}",
        )


async def _demo_request_inner(body: DemoRequestBody, request: Request) -> DemoRequestResponse:

    client_ip = (request.client.host if request.client else "unknown")[:64]

    # ── Rate limit by IP ──────────────────────────────────────────────────────
    if sqlhelper.count_demo_registrations_by_ip(client_ip) >= _RATE_LIMIT_PER_IP:
        # Return same generic message — don't reveal the limit type
        logger.warning("Demo request rate-limited by IP: %s", client_ip)
        return DemoRequestResponse(
            message="Thanks! If this email isn't already registered, you'll receive an invitation shortly."
        )

    # ── Rate limit by email (return same message to prevent enumeration) ──────
    if sqlhelper.count_demo_registrations_by_email(body.email) >= _RATE_LIMIT_PER_EMAIL:
        logger.info("Demo request duplicate email suppressed: %s", body.email)
        return DemoRequestResponse(
            message="Thanks! If this email isn't already registered, you'll receive an invitation shortly."
        )

    # ── Resolve demo tenant ───────────────────────────────────────────────────
    tenant_id = sqlhelper.get_demo_tenant_id()
    if tenant_id is None:
        logger.error("Demo tenant not found in dbo.Tenants — seed_demo.py may not have run.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo environment is not configured. Please contact the administrator.",
        )

    # ── Send B2B invitation via Graph API ─────────────────────────────────────
    try:
        invite_result = graph_admin.invite_external_user(
            first_name=body.first_name,
            last_name=body.last_name,
            email=body.email,
            invite_redirect_url=_INVITE_REDIRECT_URL,
        )
    except RuntimeError as e:
        logger.error("B2B invitation failed for %s: %s", body.email, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not send invitation. Please try again later.",
        )

    oid        = invite_result["oid"]
    upn        = invite_result["upn"]        # = body.email for B2B guests
    redeem_url = invite_result["redeem_url"]

    # ── Queue branded invitation email via Service Bus → auxiliary worker ────
    try:
        await publish_event(
            "notification.access_requested",
            extra={
                "to":         body.email,
                "first_name": body.first_name,
                "redeem_url": redeem_url,
            },
        )
        logger.info("Demo access invitation queued for %s", body.email)
    except Exception as e:
        # Non-fatal — the B2B guest account is created; the visitor can be
        # re-invited manually if the Service Bus publish fails.
        logger.error("Failed to queue demo access invitation for %s: %s", body.email, e)

    # ── Assign admin role (if requested) ──────────────────────────────────────
    if body.is_admin and oid:
        try:
            graph_admin.assign_admin_role(oid)
        except RuntimeError as e:
            # Non-fatal — the user can still sign in; admin role can be
            # granted manually if Graph is temporarily unavailable.
            logger.error("Admin role assignment failed for oid=%s: %s", oid, e)

    # ── Create dbo.Users row ──────────────────────────────────────────────────
    # Store email as UPN — this is what auth.py reads from the JWT
    # preferred_username claim for B2B guest tokens.
    if not sqlhelper.upn_exists_in_tenant(upn, tenant_id):
        try:
            sqlhelper.create_demo_user(
                first_name=body.first_name,
                last_name=body.last_name,
                email=body.email,
                upn=upn,
                tenant_id=tenant_id,
            )
        except Exception as e:
            logger.error("DB insert failed for %s: %s", upn, e)
            # Non-fatal at this stage — invitation is already sent.
            # The user's first sign-in will fail with 404; they can
            # contact us to re-try the registration.

    # ── Audit log ─────────────────────────────────────────────────────────────
    try:
        sqlhelper.log_demo_registration(
            first_name=body.first_name,
            last_name=body.last_name,
            email=body.email,
            is_admin=body.is_admin,
            aad_object_id=oid or None,
            request_ip=client_ip,
        )
    except Exception as e:
        logger.warning("Failed to log demo registration: %s", e)

    logger.info(
        "Demo invitation sent: email=%s is_admin=%s oid=%s ip=%s",
        body.email, body.is_admin, oid, client_ip,
    )

    return DemoRequestResponse(
        message="Thanks! If this email isn't already registered, you'll receive an invitation shortly."
    )
