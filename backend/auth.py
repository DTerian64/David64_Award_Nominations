"""
Authentication and Authorization Module
========================================
Handles JWT validation, tenant resolution, user authentication, and impersonation.

Multi-tenancy model
-------------------
The app is registered as AzureADMultipleOrgs (multi-tenant).  Every inbound
JWT contains a ``tid`` claim — the Azure AD tenant GUID of the organisation
that issued the token.  On each request we:

  1. Unverified-decode the JWT header/claims to extract ``tid`` and ``kid``.
  2. Resolve ``tid`` → internal ``TenantId`` via the Tenants table.
     → 403 if the tenant is not registered (not a customer).
  3. Fully verify the JWT signature using the per-tenant JWKS endpoint,
     and validate audience, issuer, and expiry.
  4. Look up the user by (UPN, TenantId).
     → 404 if the user does not exist in that tenant's roster.
  5. Return a user-context dict that includes TenantId on every request,
     so downstream query helpers can apply row-level tenant isolation.

MSAL authority
--------------
The OAuth2 scheme uses the ``/common`` endpoint so that users from any
registered tenant can sign in.  Single-tenant authority strings (e.g.
``/{TENANT_ID}/``) are rejected by Azure for multi-tenant apps.

JWKS verification
-----------------
Keys are fetched from the Microsoft Entra ID JWKS endpoint and cached
in memory by PyJWT's PyJWKClient (TTL ~5 min, re-fetched on unknown kid).
Per-tenant issuer validation (``https://login.microsoftonline.com/{tid}/v2.0``)
ensures tokens from un-registered tenants cannot be replayed even if the
``tid`` allowlist check were somehow bypassed.
"""

import os
import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, Header, status
from fastapi.security import OAuth2
from typing import Optional, Dict, Any, Callable
import sqlhelper2 as sqlhelper
import logging

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

CLIENT_ID = os.getenv("CLIENT_ID")
if not CLIENT_ID:
    raise RuntimeError(
        "CLIENT_ID environment variable is not set. "
        "This is required for JWT audience validation. "
        "Add CLIENT_ID to the Container App environment variables in Terraform."
    )

# /common accepts tokens from ANY registered tenant.
# Individual tenant authority strings break multi-tenant login.
AUTHORITY = "https://login.microsoftonline.com/common"

# JWKS client — caches signing keys in memory; re-fetches on unknown kid.
# Using /common so any tenant's keys can be resolved from a single client.
_JWKS_URI = f"{AUTHORITY}/discovery/v2.0/keys"
_jwks_client = PyJWKClient(_JWKS_URI, cache_keys=True)

# ============================================================================
# OAUTH2 SCHEME  (Swagger UI support)
# ============================================================================

oauth2_scheme = OAuth2(
    flows={
        "authorizationCode": {
            "authorizationUrl": f"{AUTHORITY}/oauth2/v2.0/authorize",
            "tokenUrl":         f"{AUTHORITY}/oauth2/v2.0/token",
            "scopes": {
                f"api://{CLIENT_ID}/access_as_user": "Access the API as the signed-in user",
                "openid": "OpenID Connect",
                "profile": "User profile",
                "email":   "User email",
            },
        }
    }
)

# ============================================================================
# AUTHENTICATION FUNCTIONS
# ============================================================================

async def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    """
    Validate a Microsoft Entra ID token, resolve the tenant, and return
    the authenticated user context.

    Verification steps:
      1. Unverified decode → extract tid (tenant) and kid (signing key ID).
      2. Validate tid against the registered-tenant allowlist (403 if unknown).
      3. Fetch the signing key from the Microsoft JWKS endpoint (cached).
      4. Fully verify the JWT: signature, audience, issuer, and expiry.
      5. Look up the user scoped to the resolved internal TenantId.

    Returns a dict with:
        UserId, userPrincipalName, FirstName, LastName, Title, ManagerId,
        TenantId, AadTenantId, roles
    """
    try:
        if token.startswith("Bearer "):
            token = token[7:]

        # ── Pass 1: unverified decode to extract tid for tenant resolution ─
        # We must know the tenant before we can validate the issuer claim,
        # and we must validate the tenant before spending effort on crypto.
        unverified_payload = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud":       False,
                "verify_iss":       False,
                "verify_exp":       False,
            },
        )

        logger.info("Token claims (unverified): %s", list(unverified_payload.keys()))
        logger.info(
            "Token aud=%r  ver=%r  scp=%r",
            unverified_payload.get("aud"),
            unverified_payload.get("ver"),
            unverified_payload.get("scp"),
        )

        # ── 1. Extract Azure AD tenant ID (tid claim) ─────────────────────
        aad_tenant_id = unverified_payload.get("tid")
        if not aad_tenant_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing tid claim in token — cannot determine tenant.",
            )

        # ── 2. Resolve tid → internal TenantId ────────────────────────────
        # Validates the tenant is a registered customer BEFORE doing any
        # crypto work. Unknown tenants are rejected early (fail-fast).
        tenant_row = sqlhelper.get_tenant_by_aad_id(aad_tenant_id)
        if not tenant_row:
            logger.warning("Unregistered tenant attempted login: tid=%s", aad_tenant_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Tenant {aad_tenant_id} is not registered with this application. "
                    "Please contact your administrator."
                ),
            )

        tenant_id   = tenant_row[0]   # internal integer TenantId
        tenant_name = tenant_row[1]
        logger.info("Resolved tenant: %s (id=%d)", tenant_name, tenant_id)

        # ── Pass 2: full cryptographic verification ────────────────────────
        # Fetch the signing key that matches the token's kid header.
        # PyJWKClient caches keys and re-fetches on cache miss.
        try:
            signing_key = _jwks_client.get_signing_key_from_jwt(token)
        except Exception as e:
            logger.error("JWKS key fetch/match failed: %s", e)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token signing key could not be verified.",
            )

        expected_issuer = f"https://login.microsoftonline.com/{aad_tenant_id}/v2.0"
        expected_audience = f"api://{CLIENT_ID}"

        logger.info(
            "Verifying token — expected_audience=%r  expected_issuer=%r",
            expected_audience,
            expected_issuer,
        )

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=expected_audience,
            issuer=expected_issuer,
            options={"verify_exp": True},
        )

        logger.info("Token fully verified — issuer: %s", expected_issuer)

        # ── 3. Extract UPN ─────────────────────────────────────────────────
        upn = (
            payload.get("upn")
            or payload.get("preferred_username")
            or payload.get("email")
        )
        if not upn:
            logger.warning("UPN not found. Available claims: %s", list(payload.keys()))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User Principal Name not found in token.",
            )

        logger.info("UPN: %s", upn)

        # ── 4. Look up user scoped to tenant ───────────────────────────────
        row = sqlhelper.get_user_by_upn_and_tenant(upn, tenant_id)
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User '{upn}' not found in tenant '{tenant_name}'.",
            )

        logger.info(
            "Authenticated: %s %s (UserId=%d, TenantId=%d)",
            row[2], row[3], row[0], tenant_id,
        )

        return {
            "UserId":             row[0],
            "userPrincipalName":  row[1],
            "FirstName":          row[2],
            "LastName":           row[3],
            "Title":              row[4],
            "ManagerId":          row[5],
            "TenantId":           tenant_id,       # internal FK
            "AadTenantId":        aad_tenant_id,   # Azure AD GUID (for logging)
            "roles":              payload.get("roles", []),
        }

    except HTTPException:
        raise
    except jwt.DecodeError as e:
        logger.error("JWT decode error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token format: {e}",
        )
    except jwt.InvalidTokenError as e:
        logger.error("Invalid token: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )
    except Exception as e:
        logger.exception("Unexpected error in get_current_user")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Authentication error: {e}",
        )


async def get_current_user_with_impersonation(
    token: str = Depends(oauth2_scheme),
    x_impersonate_user: Optional[str] = Header(
        None,
        alias="X-Impersonate-User",
        description=(
            "🎭 Admin only: UPN of the user to impersonate within your tenant "
            "(e.g. Chris.Brown@terian-services.com)"
        ),
        example="Chris.Brown@terian-services.com",
    ),
) -> Dict[str, Any]:
    """
    Handles both regular authentication and admin impersonation.

    Impersonation is tenant-scoped — an admin from Tenant A cannot
    impersonate a user in Tenant B.

    Returns:
        actual_user   — the authenticated admin (from the JWT)
        effective_user — the user to act as (impersonated or actual)
        is_impersonating — bool
    """
    actual_user = await get_current_user(token)

    if x_impersonate_user:
        # Verify admin role
        roles    = actual_user.get("roles", [])
        is_admin = "AWard_Nomination_Admin" in roles or "Administrator" in roles

        if not is_admin:
            logger.warning(
                "Non-admin %s attempted to impersonate %s",
                actual_user["userPrincipalName"],
                x_impersonate_user,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only administrators can impersonate users.",
            )

        # Look up the target user — scoped to the SAME tenant as the admin
        tenant_id = actual_user["TenantId"]
        impersonated_row = sqlhelper.get_user_by_upn_and_tenant(
            x_impersonate_user, tenant_id
        )
        if not impersonated_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"User '{x_impersonate_user}' not found in your tenant. "
                    "Cross-tenant impersonation is not permitted."
                ),
            )

        impersonated_user = {
            "UserId":            impersonated_row[0],
            "userPrincipalName": impersonated_row[1],
            "FirstName":         impersonated_row[2],
            "LastName":          impersonated_row[3],
            "Title":             impersonated_row[4],
            "ManagerId":         impersonated_row[5],
            "TenantId":          tenant_id,
            "AadTenantId":       actual_user["AadTenantId"],
            "roles":             [],  # impersonated user does not inherit admin roles
        }

        sqlhelper.log_impersonation(
            admin_upn=actual_user["userPrincipalName"],
            impersonated_upn=x_impersonate_user,
            action="impersonation_started",
        )

        logger.info(
            "✅ Admin %s is impersonating %s (TenantId=%d)",
            actual_user["userPrincipalName"],
            x_impersonate_user,
            tenant_id,
        )

        return {
            "actual_user":     actual_user,
            "effective_user":  impersonated_user,
            "is_impersonating": True,
        }

    return {
        "actual_user":     actual_user,
        "effective_user":  actual_user,
        "is_impersonating": False,
    }


# ============================================================================
# ROLE / PERMISSION HELPERS
# ============================================================================

def require_role(required_role: str) -> Callable:
    """
    Dependency factory — raises 403 if the authenticated user does not hold
    the specified app role.  Always checks actual_user (not effective_user)
    to prevent privilege escalation via impersonation.

    Example:
        @app.get("/admin/endpoint")
        async def admin_only(user = Depends(require_role("AWard_Nomination_Admin"))):
            ...
    """
    def _checker(user_claims: Dict[str, Any] = Depends(get_current_user)):
        roles = user_claims.get("roles", []) or []
        if required_role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not Authorized",
            )
        return user_claims
    return _checker


def is_admin(user: Dict[str, Any]) -> bool:
    """Return True if the user holds an admin app role."""
    roles = user.get("roles", [])
    return "AWard_Nomination_Admin" in roles or "Administrator" in roles


async def log_action_if_impersonating(
    user_context: Dict[str, Any],
    action: str,
    details: Optional[str] = None,
) -> None:
    """Log an audit entry only when an admin is actively impersonating."""
    if user_context["is_impersonating"]:
        sqlhelper.log_impersonation(
            admin_upn=user_context["actual_user"]["userPrincipalName"],
            impersonated_upn=user_context["effective_user"]["userPrincipalName"],
            action=action,
            details=details,
        )
