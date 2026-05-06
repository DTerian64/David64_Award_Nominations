"""
graph_admin.py
==============
Reusable Graph API helpers for the Demo tenant:
  • create_aad_user()       – creates an enabled @demo.terian-services.com account
  • assign_admin_role()     – assigns AWard_Nomination_Admin on the Award Nomination app

Environment variables (same as seed_demo.py):
  DEMO_AAD_TENANT_ID       – Demo tenant GUID
  DEMO_GRAPH_CLIENT_ID     – Award Nomination Seeder app client ID
  DEMO_GRAPH_CLIENT_SECRET – Seeder client secret

The service-principal and role IDs are fetched once and cached in memory for
the lifetime of the process (they never change once provisioned).
"""

import os
import re
import secrets
import string
import logging
from typing import Optional

import msal
import requests

logger = logging.getLogger(__name__)

GRAPH        = "https://graph.microsoft.com/v1.0"
UPN_SUFFIX   = "@demo.terian-services.com"
SP_NAME_HINT = "Award Nomination - sandbox"
ROLE_VALUE   = "AWard_Nomination_Admin"

# ---------------------------------------------------------------------------
# Lazy-loaded cache — set once on first call
# ---------------------------------------------------------------------------
_token_cache: dict = {}          # {"token": str, "expires_at": float}
_sp_id:    Optional[str] = None  # service principal object ID
_role_id:  Optional[str] = None  # AWard_Nomination_Admin app role ID


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _env() -> tuple[str, str, str]:
    tid    = os.environ.get("DEMO_AAD_TENANT_ID", "")
    cid    = os.environ.get("DEMO_GRAPH_CLIENT_ID", "")
    secret = os.environ.get("DEMO_GRAPH_CLIENT_SECRET", "")
    if not (tid and cid and secret):
        raise RuntimeError(
            "DEMO_AAD_TENANT_ID / DEMO_GRAPH_CLIENT_ID / DEMO_GRAPH_CLIENT_SECRET "
            "must be set in the environment to use the demo self-registration endpoint."
        )
    return tid, cid, secret


def _get_token() -> str:
    """Return a valid Graph access token, refreshing if expired."""
    import time
    global _token_cache

    now = time.time()
    if _token_cache.get("token") and _token_cache.get("expires_at", 0) > now + 60:
        return _token_cache["token"]

    tid, cid, secret = _env()
    app = msal.ConfidentialClientApplication(
        cid,
        authority=f"https://login.microsoftonline.com/{tid}",
        client_credential=secret,
    )
    result = app.acquire_token_for_client(["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(
            f"Graph token acquisition failed: {result.get('error_description', result)}"
        )
    _token_cache = {
        "token":      result["access_token"],
        "expires_at": now + result.get("expires_in", 3600),
    }
    return _token_cache["token"]


def _gh(path: str, params: dict = None) -> requests.Response:
    return requests.get(
        f"{GRAPH}/{path.lstrip('/')}",
        headers={"Authorization": f"Bearer {_get_token()}"},
        params=params,
        timeout=20,
    )


def _post(path: str, body: dict) -> requests.Response:
    return requests.post(
        f"{GRAPH}/{path.lstrip('/')}",
        headers={
            "Authorization":  f"Bearer {_get_token()}",
            "Content-Type":   "application/json",
        },
        json=body,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Service-principal + role ID (cached)
# ---------------------------------------------------------------------------

def _get_sp_and_role() -> tuple[str, str]:
    """
    Return (service_principal_id, app_role_id) for AWard_Nomination_Admin.
    Fetched once and cached for the process lifetime.
    """
    global _sp_id, _role_id
    if _sp_id and _role_id:
        return _sp_id, _role_id

    # $search requires ConsistencyLevel: eventual
    r = requests.get(
        f"{GRAPH}/servicePrincipals",
        headers={
            "Authorization":  f"Bearer {_get_token()}",
            "ConsistencyLevel": "eventual",
        },
        params={
            "$search":  f'"displayName:{SP_NAME_HINT}"',
            "$select":  "id,displayName,appRoles",
        },
        timeout=20,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"Service principal search failed: {r.status_code} {r.text}"
        )

    sps = r.json().get("value", [])
    if not sps:
        raise RuntimeError(
            f"No service principal found matching '{SP_NAME_HINT}'. "
            "Ensure the app has been consented to in the Demo tenant."
        )

    sp = sps[0]
    _sp_id = sp["id"]

    role = next(
        (ar for ar in sp.get("appRoles", []) if ar.get("value") == ROLE_VALUE),
        None,
    )
    if not role:
        available = [ar.get("value") for ar in sp.get("appRoles", [])]
        raise RuntimeError(
            f"App role '{ROLE_VALUE}' not found on service principal. "
            f"Available roles: {available}"
        )

    _role_id = role["id"]
    logger.info("Cached SP id=%s, role id=%s", _sp_id, _role_id)
    return _sp_id, _role_id


# ---------------------------------------------------------------------------
# Password generation
# ---------------------------------------------------------------------------

def _random_password(length: int = 16) -> str:
    upper   = string.ascii_uppercase
    lower   = string.ascii_lowercase
    digits  = string.digits
    special = "!@#$%&*"
    all_chars = upper + lower + digits + special

    while True:
        pwd = "".join(secrets.choice(all_chars) for _ in range(length))
        if (
            any(c in upper   for c in pwd)
            and any(c in lower   for c in pwd)
            and any(c in digits  for c in pwd)
            and any(c in special for c in pwd)
        ):
            return pwd


# ---------------------------------------------------------------------------
# UPN helpers
# ---------------------------------------------------------------------------

def _normalise_name_part(s: str) -> str:
    """Lowercase, strip accents roughly, keep only [a-z0-9]."""
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]", "", s)
    return s or "user"


def _upn_exists(upn: str) -> bool:
    r = _gh(f"users/{upn}", {"$select": "id"})
    return r.status_code == 200


def _generate_upn(first_name: str, last_name: str) -> str:
    """Return a unique @demo.terian-services.com UPN."""
    base = f"{_normalise_name_part(first_name)}.{_normalise_name_part(last_name)}"
    candidate = f"{base}{UPN_SUFFIX}"
    if not _upn_exists(candidate):
        return candidate
    for n in range(2, 100):
        candidate = f"{base}{n}{UPN_SUFFIX}"
        if not _upn_exists(candidate):
            return candidate
    raise RuntimeError("Could not generate a unique UPN after 100 attempts.")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_aad_user(first_name: str, last_name: str) -> dict:
    """
    Create an enabled AAD account in the Demo tenant.

    Returns:
        {
            "oid":           str,   # Azure AD object ID
            "upn":           str,   # userPrincipalName
            "temp_password": str,   # one-time password shown to the user
        }
    """
    upn  = _generate_upn(first_name, last_name)
    pwd  = _random_password()
    nick = upn.split("@")[0].replace(".", "")

    body = {
        "accountEnabled":    True,
        "displayName":       f"{first_name} {last_name}",
        "givenName":         first_name,
        "surname":           last_name,
        "mailNickname":      nick,
        "userPrincipalName": upn,
        "usageLocation":     "US",
        "passwordProfile": {
            "forceChangePasswordNextSignIn": False,
            "password":                      pwd,
        },
    }

    r = _post("users", body)
    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"AAD user creation failed: {r.status_code} {r.text}"
        )

    oid = r.json()["id"]
    logger.info("Created AAD user: %s (oid=%s)", upn, oid)
    return {"oid": oid, "upn": upn, "temp_password": pwd}


def assign_admin_role(user_oid: str) -> None:
    """
    Assign the AWard_Nomination_Admin app role to the given user object ID.
    Idempotent — silently succeeds if already assigned.
    """
    sp_id, role_id = _get_sp_and_role()

    body = {
        "principalId": user_oid,
        "resourceId":  sp_id,
        "appRoleId":   role_id,
    }
    r = _post(f"users/{user_oid}/appRoleAssignments", body)

    if r.status_code in (200, 201):
        logger.info(
            "Assigned %s to user oid=%s (assignment id=%s)",
            ROLE_VALUE, user_oid, r.json().get("id"),
        )
    elif r.status_code == 400 and "Permission" in r.text:
        # Already assigned — treat as success
        logger.info("Role already assigned to oid=%s — no-op.", user_oid)
    else:
        raise RuntimeError(
            f"App role assignment failed: {r.status_code} {r.text}"
        )
