"""
assign_admin_role.py
Assigns the AWard_Nomination_Admin app role to a user in the Demo tenant
using the client-credentials Graph token (no interactive login needed).

Requirements
------------
The seed app registration (DEMO_GRAPH_CLIENT_ID) needs:
    AppRoleAssignment.ReadWrite.All  (Application permission)
    Application.Read.All             (Application permission)

If those aren't granted yet, the script prints instructions and exits.

Usage
-----
    cd Award_Nomination_App/scripts
    python assign_admin_role.py

Or to assign to a different UPN:
    python assign_admin_role.py --upn someone@demo.terian-services.com
"""

import sys
import os
import argparse
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent / ".env")

import msal

# ── Config ────────────────────────────────────────────────────────────────────

TENANT_ID     = os.environ["DEMO_AAD_TENANT_ID"]
CLIENT_ID     = os.environ["DEMO_GRAPH_CLIENT_ID"]
CLIENT_SECRET = os.environ["DEMO_GRAPH_CLIENT_SECRET"]

# The UPN of the user to promote to admin in the Demo tenant
DEFAULT_UPN = "david64.terian@demo.terian-services.com"

# The app role value exactly as defined in the app manifest
APP_ROLE_VALUE = "AWard_Nomination_Admin"

# The display name of the Award Nomination app registration (as it appears
# in Enterprise Applications in the Demo tenant)
APP_DISPLAY_NAME_CONTAINS = "Award Nomination - sandbox"

GRAPH = "https://graph.microsoft.com/v1.0"

# ── Token ─────────────────────────────────────────────────────────────────────

def get_token() -> str:
    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        print(f"✗ Token failed: {result.get('error_description', result)}")
        sys.exit(1)
    return result["access_token"]


def gh(token: str, path: str, params: dict = None):
    """GET helper."""
    r = requests.get(
        f"{GRAPH}/{path.lstrip('/')}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=20,
    )
    return r


def post(token: str, path: str, body: dict):
    """POST helper."""
    r = requests.post(
        f"{GRAPH}/{path.lstrip('/')}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=20,
    )
    return r


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--upn", default=DEFAULT_UPN,
                        help="UPN of the user to assign AWard_Nomination_Admin to")
    args = parser.parse_args()

    print(f"\nAssigning {APP_ROLE_VALUE} to {args.upn}")
    print("=" * 60)

    token = get_token()
    print("✓ Token acquired")

    # ── 1. Find the user ──────────────────────────────────────────────────────
    print(f"\n[1] Looking up user: {args.upn}")
    r = gh(token, f"users/{args.upn}", {"$select": "id,displayName,userPrincipalName"})
    if r.status_code == 404:
        print(f"✗ User not found: {args.upn}")
        sys.exit(1)
    if r.status_code != 200:
        print(f"✗ User lookup failed: {r.status_code} {r.text}")
        sys.exit(1)
    user = r.json()
    user_id = user["id"]
    print(f"✓ User: {user['displayName']} (id={user_id})")

    # ── 2. Find the service principal for the Award Nomination app ────────────
    print(f"\n[2] Finding service principal for '{APP_DISPLAY_NAME_CONTAINS}'")
    r = requests.get(
        f"{GRAPH}/servicePrincipals",
        headers={
            "Authorization": f"Bearer {token}",
            "ConsistencyLevel": "eventual",
        },
        params={
            "$search": f'"displayName:{APP_DISPLAY_NAME_CONTAINS}"',
            "$select": "id,displayName,appId,appRoles",
        },
        timeout=20,
    )

    if r.status_code == 403:
        print("✗ Got 403 — the seed app is missing Application.Read.All permission.")
        print("\n  Fix: In the Demo tenant → App registrations → your seed app")
        print("       → API permissions → Add a permission → Microsoft Graph")
        print("       → Application permissions → Application.Read.All")
        print("                                  → AppRoleAssignment.ReadWrite.All")
        print("  Then click 'Grant admin consent'.")
        sys.exit(1)

    if r.status_code != 200:
        print(f"✗ Service principal search failed: {r.status_code} {r.text}")
        sys.exit(1)

    sps = r.json().get("value", [])
    if not sps:
        print(f"✗ No service principal found containing '{APP_DISPLAY_NAME_CONTAINS}'")
        print("  The app may not have been consented to in the Demo tenant yet.")
        sys.exit(1)

    # Show all matches so the user can pick the right one
    if len(sps) > 1:
        print(f"  Found {len(sps)} matches:")
        for i, sp in enumerate(sps):
            print(f"    [{i}] {sp['displayName']}  (id={sp['id']})")
        idx = int(input("  Enter the number of the correct service principal: "))
        sp = sps[idx]
    else:
        sp = sps[0]

    sp_id = sp["id"]
    print(f"✓ Service principal: {sp['displayName']} (id={sp_id})")

    # ── 3. Find the AWard_Nomination_Admin app role ID ────────────────────────
    print(f"\n[3] Looking for app role: {APP_ROLE_VALUE}")
    app_roles = sp.get("appRoles", [])
    role = next((r for r in app_roles if r.get("value") == APP_ROLE_VALUE), None)

    if not role:
        print(f"✗ App role '{APP_ROLE_VALUE}' not found on this service principal.")
        print(f"  Available roles: {[r.get('value') for r in app_roles]}")
        sys.exit(1)

    role_id = role["id"]
    print(f"✓ Role: {role['displayName']} (id={role_id})")

    # ── 4. Check if already assigned ─────────────────────────────────────────
    print(f"\n[4] Checking for existing assignment...")
    r = gh(token, f"users/{user_id}/appRoleAssignments",
           {"$filter": f"resourceId eq {sp_id}"})
    if r.status_code == 200:
        existing = r.json().get("value", [])
        already = [a for a in existing if a.get("appRoleId") == role_id]
        if already:
            print(f"✓ Role already assigned — nothing to do.")
            sys.exit(0)

    # ── 5. Assign the role ────────────────────────────────────────────────────
    print(f"\n[5] Assigning role...")
    body = {
        "principalId": user_id,
        "resourceId":  sp_id,
        "appRoleId":   role_id,
    }
    r = post(token, f"users/{user_id}/appRoleAssignments", body)

    if r.status_code in (200, 201):
        result = r.json()
        print(f"✓ Role assigned successfully!")
        print(f"  Assignment ID: {result.get('id')}")
    elif r.status_code == 403:
        print("✗ Got 403 — the seed app is missing AppRoleAssignment.ReadWrite.All permission.")
        print("\n  Fix: In the Demo tenant → App registrations → your seed app")
        print("       → API permissions → Add a permission → Microsoft Graph")
        print("       → Application permissions → AppRoleAssignment.ReadWrite.All")
        print("  Then click 'Grant admin consent'.")
        sys.exit(1)
    else:
        print(f"✗ Assignment failed: {r.status_code} {r.text}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print(f"  ✓ {args.upn} now has {APP_ROLE_VALUE} in the Demo tenant")
    print("=" * 60)


if __name__ == "__main__":
    main()