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
import os
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
    min_award:            Optional[int] = None
    max_award:            Optional[int] = None


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


# ── Award Categories ──────────────────────────────────────────────────────────
# Categories are soft-deleted (is_active), never removed, so historic nominations
# keep resolving them. Per-category limits must sit within the org award range.

class CategoryPayload(BaseModel):
    category_description: str
    min_amount: Optional[int] = None
    max_amount: Optional[int] = None
    is_active:  bool = True


def _org_award_range(tenant_id: int):
    org = sqlhelper.get_organization_settings(tenant_id)
    org_min = org.get("min_award")
    org_max = org.get("max_award")
    return (
        org_min if org_min is not None else 50,
        org_max if org_max is not None else 5000,
        org.get("currency") or "USD",
    )


def _validate_category(payload: "CategoryPayload", tenant_id: int) -> None:
    if not payload.category_description or not payload.category_description.strip():
        raise HTTPException(status_code=422, detail="Category name is required.")
    lo, hi = payload.min_amount, payload.max_amount
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(status_code=422, detail="Category minimum cannot exceed its maximum.")
    org_min, org_max, _ = _org_award_range(tenant_id)
    if lo is not None and lo < org_min:
        raise HTTPException(status_code=422, detail=f"Category minimum ({lo}) is below the organization minimum ({org_min}).")
    if hi is not None and hi > org_max:
        raise HTTPException(status_code=422, detail=f"Category maximum ({hi}) exceeds the organization maximum ({org_max}).")


@router.get("/api/admin/setup/categories")
async def list_categories(admin: dict = Depends(require_setup_admin)):
    tid = admin["TenantId"]
    org_min, org_max, currency = _org_award_range(tid)
    return {
        "currency": currency,
        "org_min_award": org_min,
        "org_max_award": org_max,
        "categories": sqlhelper.get_categories_admin(tid),
    }


@router.post("/api/admin/setup/categories")
async def create_category(payload: CategoryPayload, admin: dict = Depends(require_setup_admin)):
    _validate_category(payload, admin["TenantId"])
    cid = sqlhelper.create_category(
        admin["TenantId"], payload.category_description.strip(),
        payload.min_amount, payload.max_amount, payload.is_active,
        admin.get("userPrincipalName", "unknown"),
    )
    logger.info("Category created", extra={"tenant_id": admin["TenantId"], "category_id": cid})
    return {"id": cid}


@router.put("/api/admin/setup/categories/{category_id}")
async def update_category(category_id: int, payload: CategoryPayload,
                          admin: dict = Depends(require_setup_admin)):
    if not sqlhelper.category_in_tenant(category_id, admin["TenantId"]):
        raise HTTPException(status_code=404, detail="Category not found in your organization.")
    _validate_category(payload, admin["TenantId"])
    sqlhelper.update_category(
        category_id, admin["TenantId"], payload.category_description.strip(),
        payload.min_amount, payload.max_amount, payload.is_active,
        admin.get("userPrincipalName", "unknown"),
    )
    logger.info("Category updated", extra={"tenant_id": admin["TenantId"], "category_id": category_id})
    return {"ok": True}


# ── Fraud / Integrity ─────────────────────────────────────────────────────────
# Edits desc_check_config + integrity_config. NOTE: the integrity-check service
# caches these for its process lifetime, so changes take effect on its next
# restart (documented behaviour; hot-reload is deferred to the fraud project).

class FraudConfig(BaseModel):
    # Fraud score routing (0..100 cutoffs)
    low_threshold:      int
    medium_threshold:   int
    high_threshold:     int
    critical_threshold: int
    detection_window_days: int
    # Description quality
    use_char_count:                 bool
    min_char_count:                 int
    min_word_count:                 int
    category_alignment_threshold:   float
    duplicate_similarity_threshold: float
    llm_category_check_enabled:     bool
    llm_fit_threshold:              float
    llm_instructions:               Optional[str] = None
    boilerplate_phrases:            list = []


def _validate_fraud(p: "FraudConfig") -> None:
    routing = [p.low_threshold, p.medium_threshold, p.high_threshold, p.critical_threshold]
    if not all(0 <= x <= 100 for x in routing):
        raise HTTPException(status_code=422, detail="Score thresholds must be between 0 and 100.")
    if not (p.low_threshold <= p.medium_threshold <= p.high_threshold <= p.critical_threshold):
        raise HTTPException(status_code=422,
                            detail="Score thresholds must be non-decreasing: low <= medium <= high <= critical.")
    for name, val in (("Category alignment", p.category_alignment_threshold),
                      ("Duplicate similarity", p.duplicate_similarity_threshold),
                      ("LLM fit", p.llm_fit_threshold)):
        if not (0.0 <= val <= 1.0):
            raise HTTPException(status_code=422, detail=f"{name} threshold must be between 0 and 1.")
    if p.min_char_count < 0 or p.min_word_count < 0 or p.detection_window_days < 1:
        raise HTTPException(status_code=422, detail="Counts and the detection window must be positive.")


@router.get("/api/admin/setup/fraud")
async def get_fraud(admin: dict = Depends(require_setup_admin)):
    return sqlhelper.get_fraud_settings(admin["TenantId"])


@router.put("/api/admin/setup/fraud")
async def update_fraud(payload: FraudConfig, admin: dict = Depends(require_setup_admin)):
    _validate_fraud(payload)
    sqlhelper.update_fraud_settings(
        admin["TenantId"], payload.dict(), admin.get("userPrincipalName", "unknown"),
    )
    logger.info("Fraud/integrity config updated", extra={"tenant_id": admin["TenantId"]})
    return sqlhelper.get_fraud_settings(admin["TenantId"])


# ── Payroll Integration ───────────────────────────────────────────────────────
# Shows the tenant's configured provider + connection status. Connecting runs the
# provider OAuth on the payroll-broker (a browser redirect); disconnecting deletes
# the token here (payroll_tokens is temporal, so the removal is audited).

_BROKER_URL = os.getenv("PAYROLL_BROKER_BASE_URL", "").rstrip("/")


@router.get("/api/admin/setup/payroll")
async def get_payroll(admin: dict = Depends(require_setup_admin)):
    tid = admin["TenantId"]
    status_obj = sqlhelper.get_payroll_setup(tid)
    status_obj["authorize_url"] = None
    if status_obj["provider"] and _BROKER_URL:
        status_obj["authorize_url"] = f"{_BROKER_URL}/{status_obj['provider']['name']}/authorize?tenant_id={tid}"
    return status_obj


@router.post("/api/admin/setup/payroll/disconnect")
async def disconnect_payroll(admin: dict = Depends(require_setup_admin)):
    ok = sqlhelper.disconnect_payroll(admin["TenantId"], admin.get("userPrincipalName", "unknown"))
    logger.info("Payroll disconnected", extra={"tenant_id": admin["TenantId"], "removed": ok})
    return {"ok": ok}


# ── Audit & Access Review ─────────────────────────────────────────────────────
# Read-only SOC 2 views over the tenant's own data: the current access snapshot
# (who holds which app role), the full role change timeline (from UserRoles
# temporal history), and the impersonation audit trail. Nothing here mutates
# state; each query is scoped to the admin's own tenant from the token.

_AUDIT_HISTORY_LIMIT = 300


@router.get("/api/admin/setup/audit/access-review")
async def audit_access_review(admin: dict = Depends(require_setup_admin)):
    """Current app-role assignments for the admin's tenant (access-review snapshot)."""
    return {
        "note": "App-managed roles only. AWard_Nomination_Admin is managed in Microsoft Entra.",
        "rows": sqlhelper.get_access_review(admin["TenantId"]),
    }


@router.get("/api/admin/setup/audit/role-history")
async def audit_role_history(admin: dict = Depends(require_setup_admin)):
    """Grant/revoke timeline from UserRoles system-versioned history (own tenant)."""
    return {"rows": sqlhelper.get_role_change_history(admin["TenantId"], _AUDIT_HISTORY_LIMIT)}


@router.get("/api/admin/setup/audit/impersonation")
async def audit_impersonation(admin: dict = Depends(require_setup_admin)):
    """Impersonation audit trail for admins in the caller's tenant."""
    return {"rows": sqlhelper.get_impersonation_audit(admin["TenantId"], _AUDIT_HISTORY_LIMIT)}
