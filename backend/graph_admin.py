"""
graph_admin.py
==============
Graph API helpers for the Demo tenant:
  • invite_external_user()  – sends a B2B invitation via POST /invitations
  • assign_admin_role()     – assigns AWard_Nomination_Admin on the Award Nomination app

Microsoft sends the invitation email automatically (sendInvitationMessage=True),
so no separate email infrastructure is required.

Environment variables:
  DEMO_AAD_TENANT_ID       – Demo tenant GUID
  DEMO_GRAPH_CLIENT_ID     – Award Nomination Seeder app client ID
  DEMO_GRAPH_CLIENT_SECRET – Seeder client secret

The service-principal and role IDs are fetched once and cached in memory.
"""

import os
import logging
import time
from typing import Optional

import msal
import requests

logger = logging.getLogger(__name__)

GRAPH        = "https://graph.microsoft.com/v1.0"
SP_NAME_HINT = "Award Nomination - sandbox"
ROLE_VALUE   = "AWard_Nomination_Admin"

# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------
_token_cache: dict = {}
_sp_id:    Optional[str] = None
_role_id:  Optional[str] = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _env() -> tuple[str, str, str]:
    tid    = os.environ.get("DEMO_AAD_TENANT_ID", "")
    cid    = os.environ.get("DEMO_GRAPH_CLIENT_ID", "")
    secret = os.environ.get("DEMO_GRAPH_CLIENT_SECRET", "")
    if not (tid and cid and secret):
        raise RuntimeError(
            "DEMO_AAD_TENANT_ID / DEMO_GRAPH_CLIENT_ID / DEMO_GRAPH_CLIENT_SECRET "
            "must be set to use the demo self-registration endpoint."
        )
    return tid, cid, secret


def _get_token() -> str:
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
            "Authorization": f"Bearer {_get_token()}",
            "Content-Type":  "application/json",
        },
        json=body,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Service-principal + role ID (cached)
# ---------------------------------------------------------------------------

def _get_sp_and_role() -> tuple[str, str]:
    global _sp_id, _role_id
    if _sp_id and _role_id:
        return _sp_id, _role_id

    r = requests.get(
        f"{GRAPH}/servicePrincipals",
        headers={
            "Authorization":    f"Bearer {_get_token()}",
            "ConsistencyLevel": "eventual",
        },
        params={
            "$search": f'"displayName:{SP_NAME_HINT}"',
            "$select": "id,displayName,appRoles",
        },
        timeout=20,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Service principal search failed: {r.status_code} {r.text}")

    sps = r.json().get("value", [])
    if not sps:
        raise RuntimeError(
            f"No service principal found matching '{SP_NAME_HINT}'. "
            "Ensure the app has been consented to in the Demo tenant."
        )

    sp     = sps[0]
    _sp_id = sp["id"]

    role = next(
        (ar for ar in sp.get("appRoles", []) if ar.get("value") == ROLE_VALUE),
        None,
    )
    if not role:
        available = [ar.get("value") for ar in sp.get("appRoles", [])]
        raise RuntimeError(
            f"App role '{ROLE_VALUE}' not found. Available: {available}"
        )

    _role_id = role["id"]
    logger.info("Cached SP id=%s, role id=%s", _sp_id, _role_id)
    return _sp_id, _role_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def invite_external_user(
    first_name: str,
    last_name:  str,
    email:      str,
    invite_redirect_url: str,
) -> dict:
    """
    Send a B2B invitation to an external email address.

    Microsoft sends the invitation email on our behalf with the standard
    "You've been invited" branding.  The email is trusted by spam filters
    because it originates from Microsoft, not our own SMTP server.

    Returns:
        {
            "oid": str,   # guest object ID in the Demo tenant
            "upn": str,   # the email address (used as UPN in dbo.Users)
        }
    """
    body = {
        "invitedUserEmailAddress": email,
        "invitedUserDisplayName":  f"{first_name} {last_name}",
        "inviteRedirectUrl":       invite_redirect_url,
        "sendInvitationMessage":   True,
        "invitedUserMessageInfo": {
            "customizedMessageBody": (
                f"Hi {first_name},\n\n"
                "You've been invited to explore the Award Nominations demo — a live "
                "SaaS platform for employee recognition and monetary award management.\n\n"
                "Click 'Accept invitation' below to get instant access. "
                "No setup required — you'll be guided through the next steps automatically."
            ),
        },
    }

    r = _post("invitations", body)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"B2B invitation failed: {r.status_code} {r.text}")

    data = r.json()
    oid  = data.get("invitedUser", {}).get("id", "")

    logger.info("B2B invitation sent to %s (guest oid=%s)", email, oid)
    return {"oid": oid, "upn": email}


def assign_admin_role(user_oid: str) -> None:
    """
    Assign AWard_Nomination_Admin to the given guest object ID.
    Idempotent — silently succeeds if already assigned.
    """
    sp_id, role_id = _get_sp_and_role()

    r = _post(
        f"users/{user_oid}/appRoleAssignments",
        {
            "principalId": user_oid,
            "resourceId":  sp_id,
            "appRoleId":   role_id,
        },
    )

    if r.status_code in (200, 201):
        logger.info("Assigned %s to oid=%s (assignment=%s)",
                    ROLE_VALUE, user_oid, r.json().get("id"))
    elif r.status_code == 400 and "Permission" in r.text:
        logger.info("Role already assigned to oid=%s — no-op.", user_oid)
    else:
        raise RuntimeError(
            f"App role assignment failed: {r.status_code} {r.text}"
        )
