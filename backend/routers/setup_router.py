"""
Setup / admin configuration endpoints — AWard_Nomination_Admin, own tenant only.

Every action is scoped to the authenticated admin's OWN tenant (derived from the
token, never from the client) and is rejected while impersonating: an admin
configures the real tenant as themselves, not "as" another user. All writes land
on tables that are temporally versioned (SOC 2), so changes are auto-audited.

Sub-areas (built incrementally): Organization, Roles & Access, Award Categories,
Fraud / Integrity, Payroll Integration.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

import utils.sqlhelper2 as sqlhelper
from auth import get_current_user, is_admin

logger = logging.getLogger(__name__)
router = APIRouter(tags=["setup"])


async def require_setup_admin(
    current_user: dict = Depends(get_current_user),
    x_impersonate_user: Optional[str] = Header(None, alias="X-Impersonate-User"),
) -> dict:
    """Gate: caller is an admin, acting on their own tenant, and NOT impersonating.

    Uses the actual authenticated user (get_current_user ignores impersonation),
    and hard-rejects if an impersonation header is present so a hidden UI control
    can never be bypassed.
    """
    if x_impersonate_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Setup is disabled while impersonating another user.",
        )
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AWard_Nomination_Admin access required.",
        )
    return current_user


# ── Organization ──────────────────────────────────────────────────────────────

class OrganizationUpdate(BaseModel):
    tenant_name:          str
    tagline:              Optional[str] = None
    company_logo_url:     Optional[str] = None
    site_url:             Optional[str] = None
    fallback_admin_email: Optional[str] = None
    primary_color:        Optional[str] = None
    locale:               Optional[str] = None
    currency:             Optional[str] = None


@router.get("/api/admin/setup/organization")
async def get_organization(admin: dict = Depends(require_setup_admin)):
    """Return the admin's own organization (tenant) settings."""
    return sqlhelper.get_organization_settings(admin["TenantId"])


@router.put("/api/admin/setup/organization")
async def update_organization(
    payload: OrganizationUpdate,
    admin: dict = Depends(require_setup_admin),
):
    """Update the admin's OWN organization settings. Tenant is taken from the token."""
    if not payload.tenant_name or not payload.tenant_name.strip():
        raise HTTPException(status_code=422, detail="Organization name is required.")
    sqlhelper.update_organization_settings(
        admin["TenantId"], payload.dict(), admin.get("userPrincipalName", "unknown"),
    )
    logger.info(
        "Organization settings updated",
        extra={"tenant_id": admin["TenantId"], "by": admin.get("userPrincipalName")},
    )
    return sqlhelper.get_organization_settings(admin["TenantId"])


# ── Roles & Access ────────────────────────────────────────────────────────────
# App roles only. AWard_Nomination_Admin is Entra-managed and intentionally NOT
# grantable here — admins can't self-elevate or mint other admins from the app.

_ASSIGNABLE_ROLES = {"HRBP", "PayrollBP", "Support"}


class RoleChange(BaseModel):
    user_id: int
    role: str


def _validate_role_change(payload: "RoleChange", admin: dict) -> None:
    if payload.role not in _ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Role '{payload.role}' cannot be managed here.",
        )
    # Own-tenant enforcement on the TARGET user (not just the caller).
    if not sqlhelper.user_in_tenant(payload.user_id, admin["TenantId"]):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in your organization.",
        )


@router.get("/api/admin/setup/roles")
async def list_roles(admin: dict = Depends(require_setup_admin)):
    """Current app-role assignments + the tenant's users (for the grant picker)."""
    tid = admin["TenantId"]
    return {
        "assignable_roles": sorted(_ASSIGNABLE_ROLES),
        "members": sqlhelper.get_tenant_role_members(tid),
        "users":   sqlhelper.get_tenant_users_brief(tid),
    }


@router.post("/api/admin/setup/roles/grant")
async def grant_role(payload: RoleChange, admin: dict = Depends(require_setup_admin)):
    _validate_role_change(payload, admin)
    sqlhelper.assign_user_role(payload.user_id, payload.role, admin["UserId"])
    logger.info(
        "Role granted",
        extra={"tenant_id": admin["TenantId"], "target_user": payload.user_id,
               "role": payload.role, "by": admin.get("userPrincipalName")},
    )
    return {"ok": True}


@router.post("/api/admin/setup/roles/revoke")
async def revoke_role(payload: RoleChange, admin: dict = Depends(require_setup_admin)):
    _validate_role_change(payload, admin)
    sqlhelper.revoke_user_role(payload.user_id, payload.role)
    logger.info(
        "Role revoked",
        extra={"tenant_id": admin["TenantId"], "target_user": payload.user_id,
               "role": payload.role, "by": admin.get("userPrincipalName")},
    )
    return {"ok": True}
