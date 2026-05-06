"""
test_graph_connection.py
Quick smoke-test for the DEMO_* Graph API credentials.
Run from the Award_Nomination_App directory:
    python scripts/test_graph_connection.py
"""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent / ".env")

import os
import sys
import msal
import requests

# ── 1. Check env vars ────────────────────────────────────────────────────────

required = ["DEMO_AAD_TENANT_ID", "DEMO_GRAPH_CLIENT_ID", "DEMO_GRAPH_CLIENT_SECRET"]
missing  = [k for k in required if not os.environ.get(k, "").strip()]
if missing:
    print(f"✗ Missing env vars: {missing}")
    print("  Add them to your .env file and try again.")
    sys.exit(1)

tid    = os.environ["DEMO_AAD_TENANT_ID"]
cid    = os.environ["DEMO_GRAPH_CLIENT_ID"]
secret = os.environ["DEMO_GRAPH_CLIENT_SECRET"]

print(f"  Tenant ID : {tid}")
print(f"  Client ID : {cid}")
print(f"  Secret    : ***{secret[-4:]}")

# ── 2. Acquire token ─────────────────────────────────────────────────────────

print("\n[1] Acquiring Graph token...")
app = msal.ConfidentialClientApplication(
    cid,
    authority=f"https://login.microsoftonline.com/{tid}",
    client_credential=secret,
)
result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])

if "access_token" not in result:
    print(f"✗ Token acquisition failed: {result.get('error_description', result)}")
    sys.exit(1)

token = result["access_token"]
print("✓ Token acquired")

# ── 3. List users in tenant ──────────────────────────────────────────────────

print("\n[2] Calling Graph /users...")
r = requests.get(
    "https://graph.microsoft.com/v1.0/users",
    headers={"Authorization": f"Bearer {token}"},
    params={"$select": "userPrincipalName,accountEnabled", "$top": "5"},
    timeout=15,
)

if r.status_code != 200:
    print(f"✗ /users call failed: {r.status_code} {r.text}")
    sys.exit(1)

users = r.json().get("value", [])
print(f"✓ /users returned {len(users)} user(s):")
for u in users:
    print(f"    {u['userPrincipalName']}  (enabled={u['accountEnabled']})")

# ── 4. Test user creation (dry-run — immediately deletes) ────────────────────

print("\n[3] Testing user create + delete (canary account)...")
TEST_UPN = "seed.test.canary@demo.terian-services.com"

body = {
    "accountEnabled":    False,
    "displayName":       "Seed Test Canary",
    "givenName":         "Seed",
    "surname":           "Canary",
    "mailNickname":      "seed.test.canary",
    "userPrincipalName": TEST_UPN,
    "usageLocation":     "US",
    "passwordProfile": {
        "forceChangePasswordNextSignIn": False,
        "password": "T3stC@n@ry!Seed99",
    },
}

# Delete if already exists from a previous aborted test
r_check = requests.get(
    f"https://graph.microsoft.com/v1.0/users/{TEST_UPN}",
    headers={"Authorization": f"Bearer {token}"},
    timeout=15,
)
if r_check.status_code == 200:
    oid = r_check.json()["id"]
    requests.delete(
        f"https://graph.microsoft.com/v1.0/users/{oid}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    print("  (cleaned up leftover canary from previous run)")

r_create = requests.post(
    "https://graph.microsoft.com/v1.0/users",
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    json=body,
    timeout=30,
)
if r_create.status_code not in (200, 201):
    print(f"✗ User create failed: {r_create.status_code} {r_create.text}")
    sys.exit(1)

oid = r_create.json()["id"]
print(f"✓ Created canary user: {TEST_UPN}  (id={oid})")

# Delete immediately
r_del = requests.delete(
    f"https://graph.microsoft.com/v1.0/users/{oid}",
    headers={"Authorization": f"Bearer {token}"},
    timeout=15,
)
if r_del.status_code == 204:
    print(f"✓ Deleted canary user")
else:
    print(f"  Warning: delete returned {r_del.status_code} — clean up manually")

# ── Summary ──────────────────────────────────────────────────────────────────

print("\n" + "=" * 50)
print("  ✓ All checks passed — ready to run seed_demo.py")
print("=" * 50)
