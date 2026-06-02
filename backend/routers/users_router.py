"""
routers/users_router.py
=======================
User identity, tenant config, and user-list endpoints.

Routes
------
GET /api/me               — effective user identity + app roles
GET /api/users            — all users in tenant (for nomination form)
GET /api/tenant/config    — per-tenant UI config (locale, currency, categories)
"""

import logging
import json as _json

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List

import utils.sqlhelper2 as sqlhelper
from auth import get_current_user_with_impersonation, log_action_if_impersonating, is_admin
from routers.schemas import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["users"])


@router.get("/api/me")
async def get_me(user_context: dict = Depends(get_current_user_with_impersonation)):
    """
    Return the effective user's identity and application roles.

    - app_roles: roles from dbo.UserRoles (e.g. ['HRBP']) for the effective user.
      The frontend uses this to conditionally show the HRBP tab.
    - is_admin: derived from the Azure AD token of the *actual* user (not the
      impersonated one) — admins retain their own identity for Analytics access.
    """
    effective_user = user_context["effective_user"]
    actual_user    = user_context["actual_user"]

    app_roles = sqlhelper.get_user_roles(effective_user["UserId"])

    return {
        "user_id":    effective_user["UserId"],
        "upn":        effective_user["userPrincipalName"],
        "tenant_id":  effective_user["TenantId"],
        "app_roles":  app_roles,
        "is_hrbp":    "HRBP" in app_roles,
        "is_admin":   is_admin(actual_user),
    }


@router.get("/api/users", response_model=List[User])
async def get_users(user_context: dict = Depends(get_current_user_with_impersonation)):
    """Get all users for nomination selection"""
    effective_user = user_context["effective_user"]
    tenant_id      = effective_user["TenantId"]

    rows = sqlhelper.get_all_users_except(effective_user["UserId"], tenant_id)

    users = []
    for row in rows:
        users.append(User(
            UserId=row[0],
            userPrincipalName=row[1],
            FirstName=row[2],
            LastName=row[3],
            Title=row[4],
            ManagerId=row[5]
        ))

    await log_action_if_impersonating(user_context, "viewed_users")
    return users


@router.get("/api/tenant/config")
async def get_tenant_config(user_context: dict = Depends(get_current_user_with_impersonation)):
    """
    Return the per-tenant UI configuration (locale, currency, theme).
    Returns an empty object when no config has been set; frontend falls back
    to hardcoded defaults and logs a warning of its own.
    """
    actual_user = user_context["actual_user"]
    tenant_id   = actual_user["TenantId"]
    upn         = actual_user.get("userPrincipalName", "unknown")

    logger.debug(
        "tenant_config: fetching config for tenant_id=%d upn=%s",
        tenant_id, upn,
    )

    try:
        raw = sqlhelper.get_tenant_config(tenant_id)
    except Exception as exc:
        logger.error(
            "tenant_config: DB error retrieving config for tenant_id=%d — %s. "
            "Returning empty config; frontend will use defaults.",
            tenant_id, exc,
        )
        return {}

    if raw is None:
        logger.warning(
            "tenant_config: no Config row found for tenant_id=%d (NULL or missing). "
            "Returning empty config; frontend will use defaults.",
            tenant_id,
        )
        return {}

    try:
        parsed = _json.loads(raw)
        logger.debug(
            "tenant_config: returning config for tenant_id=%d — "
            "locale=%s currency=%s primaryColor=%s",
            tenant_id,
            parsed.get("locale",             "?"),
            parsed.get("currency",           "?"),
            parsed.get("theme", {}).get("primaryColor", "?"),
        )

        # Inject the tenant's canonical domain so the frontend can redirect
        # users who land on the wrong hostname before they interact with the app.
        domain = sqlhelper.get_tenant_domain(tenant_id)
        if domain:
            parsed["domain"] = domain

        # Inject custom nomination categories (Premium/Enterprise feature).
        # Empty list → tenant has no categories → frontend hides the field.
        cat_rows = sqlhelper.get_nomination_categories(tenant_id)
        parsed["nomination_categories"] = [
            {"id": row[0], "category_description": row[1]}
            for row in cat_rows
        ]

        return parsed
    except Exception as exc:
        logger.error(
            "tenant_config: invalid JSON in Config column for tenant_id=%d — %s. "
            "Returning empty config; frontend will use defaults.",
            tenant_id, exc,
        )
        return {}
