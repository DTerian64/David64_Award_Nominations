"""
seed_demo.py
============
Seeds the "Terian Services Demo" tenant with realistic showcase data
for demonstrating the Award Nomination app to prospective SaaS customers.

What it creates
---------------
  AAD     : 100 disabled user accounts in the "Terian Services Demo" Entra tenant
             (demo.terian-services.com must be a verified custom domain in that tenant)
  Tenant  : Terian Services Demo row in dbo.Tenants  (demo-awards.terian-services.com)
  Users   : 100 fictional employees across 6 departments in dbo.Users
             UPNs   → firstname.lastname@demo.terian-services.com
             Emails → dterian64@outlook.com  (all notifications route to David)
  Nominations : 400 over 18 months with varied description text
  FraudScores : Pre-populated for every nomination (no need to wait for Monday run)
  GraphPatternFindings : Pre-seeded fraud patterns visible in the Integrity tab
             ▸ 3 nomination rings (3-, 4-, 5-person directed cycles)
             ▸ 2 approver-affinity clusters
             ▸ 8 copy-paste nomination clusters
             ▸ 4 transactional-language clusters

Prerequisites
-------------
  1. Create a new Azure AD tenant: "Terian Services Demo"
  2. Add demo.terian-services.com as a verified custom domain (DNS TXT record)
  3. Register an app in that tenant with Microsoft Graph → User.ReadWrite.All
     (application permission, admin-consented)
  4. Create a client secret for the app registration

Usage
-----
  python seed_demo.py            # idempotent — no-op if tenant already exists
  python seed_demo.py --reset    # delete all demo data (AAD + DB) and re-seed
  python seed_demo.py --dry-run  # print plan only — no writes to AAD or DB

Environment variables
---------------------
  # SQL (same as fraud-analytics-job)
  SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD
  DB_DRIVER          optional, defaults to "{ODBC Driver 18 for SQL Server}"

  # Microsoft Graph (for the new Demo AAD tenant)
  DEMO_AAD_TENANT_ID      UUID of the new Entra tenant (from Azure Portal → Overview)
  DEMO_GRAPH_CLIENT_ID    App registration client ID (User.ReadWrite.All permission)
  DEMO_GRAPH_CLIENT_SECRET Client secret for that app registration
"""

import argparse
import hashlib
import json
import os
import random
import secrets
import string
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyodbc
import requests as http_requests
from dotenv import load_dotenv

# Search for .env starting from the script's directory upward —
# finds Award_Nomination_App/.env regardless of where the script is invoked from.
load_dotenv(Path(__file__).parent / ".env")           # scripts/.env (if present)
load_dotenv(Path(__file__).parent.parent / ".env")    # Award_Nomination_App/.env

# ── Constants ─────────────────────────────────────────────────────────────────

DEMO_TENANT_NAME = "Terian Services Demo"
DEMO_DOMAIN      = "demo-awards.terian-services.com"
DEMO_UPN_SUFFIX  = "@demo.terian-services.com"
DEMO_EMAIL       = "dterian64@outlook.com"   # all demo notifications route to David
CURRENCY         = "USD"

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# 18-month window ending April 2026
EPOCH_START = datetime(2024, 11, 1)
EPOCH_END   = datetime(2026, 4, 30)

DEMO_TENANT_CONFIG = json.dumps({
    "locale":   "en-US",
    "currency": "USD",
    "theme": {
        "primaryColor":      "#7c3aed",   # violet-600 — visually distinct from other tenants
        "primaryHoverColor": "#6d28d9",   # violet-700
        "primaryLightColor": "#ede9fe",   # violet-100
        "primaryTextOnDark": "#ffffff",
    },
})

# ── Microsoft Graph helpers ───────────────────────────────────────────────────

def _graph_env() -> tuple[str, str, str]:
    """Return (tenant_id, client_id, client_secret) from env — raises if missing."""
    tid    = os.environ.get("DEMO_AAD_TENANT_ID", "").strip()
    cid    = os.environ.get("DEMO_GRAPH_CLIENT_ID", "").strip()
    secret = os.environ.get("DEMO_GRAPH_CLIENT_SECRET", "").strip()
    missing = [k for k, v in [
        ("DEMO_AAD_TENANT_ID", tid),
        ("DEMO_GRAPH_CLIENT_ID", cid),
        ("DEMO_GRAPH_CLIENT_SECRET", secret),
    ] if not v]
    if missing:
        raise EnvironmentError(
            f"Missing required env vars for Graph API: {', '.join(missing)}\n"
            "Set them in your .env file or shell before running this script."
        )
    return tid, cid, secret


_graph_token_cache: dict = {}   # simple in-process cache


def _get_graph_token() -> str:
    """
    Acquire a Microsoft Graph access token via client-credentials flow.
    Caches the token for its lifetime minus a 60-second safety margin.
    """
    import msal   # imported here so the rest of the script works without msal installed

    now = time.time()
    cached = _graph_token_cache.get("token")
    if cached and _graph_token_cache.get("expires_at", 0) > now:
        return cached

    tid, cid, secret = _graph_env()
    app = msal.ConfidentialClientApplication(
        cid,
        authority=f"https://login.microsoftonline.com/{tid}",
        client_credential=secret,
    )
    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" not in result:
        raise RuntimeError(
            f"Graph token acquisition failed: {result.get('error_description', result)}"
        )
    _graph_token_cache["token"]      = result["access_token"]
    _graph_token_cache["expires_at"] = now + result.get("expires_in", 3600) - 60
    return result["access_token"]


def _gh(token: str) -> dict:
    """Return Authorization + Content-Type headers for Graph requests."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }


def _random_password() -> str:
    """
    Generate a strong random password that meets AAD complexity requirements.
    The fictional accounts are DISABLED so this password is never used.
    """
    upper   = secrets.choice(string.ascii_uppercase)
    lower   = secrets.choice(string.ascii_lowercase)
    digit   = secrets.choice(string.digits)
    special = secrets.choice("!@#$%^&*")
    rest    = [secrets.choice(string.ascii_letters + string.digits + "!@#$%") for _ in range(20)]
    chars   = list(upper + lower + digit + special) + rest
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _aad_upn_exists(token: str, upn: str) -> tuple[bool, str | None]:
    """
    Check if a user with the given UPN exists in AAD.
    Returns (exists: bool, object_id: str | None).
    """
    r = http_requests.get(
        f"{GRAPH_BASE}/users/{upn}",
        headers=_gh(token),
        params={"$select": "id"},
        timeout=30,
    )
    if r.status_code == 404:
        return False, None
    r.raise_for_status()
    return True, r.json()["id"]


def create_aad_user(token: str, first: str, last: str, upn: str) -> str:
    """
    Create a DISABLED AAD user account for a fictional demo employee.
    Disabled means the account is a valid AAD object but cannot sign in —
    it exists only so that it can be targeted by admin impersonation.

    Returns the AAD object ID (GUID).
    Idempotent: if the UPN already exists the existing object ID is returned.
    """
    exists, oid = _aad_upn_exists(token, upn)
    if exists:
        return oid

    body = {
        "accountEnabled":    False,   # cannot sign in — impersonation only
        "displayName":       f"{first} {last}",
        "givenName":         first,
        "surname":           last,
        "mailNickname":      f"{first.lower()}.{last.lower()}",
        "userPrincipalName": upn,
        "usageLocation":     "US",
        "passwordProfile": {
            "forceChangePasswordNextSignIn": False,
            "password": _random_password(),
        },
    }
    r = http_requests.post(
        f"{GRAPH_BASE}/users",
        headers=_gh(token),
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["id"]


def delete_aad_user(token: str, upn: str) -> bool:
    """
    Permanently delete an AAD user by UPN.
    Returns True if deleted, False if the user was not found.
    Note: deleted users go to the Deleted Users recycle bin for 30 days.
    """
    exists, oid = _aad_upn_exists(token, upn)
    if not exists:
        return False
    r = http_requests.delete(
        f"{GRAPH_BASE}/users/{oid}",
        headers=_gh(token),
        timeout=30,
    )
    r.raise_for_status()
    return True


# ── Connection ─────────────────────────────────────────────────────────────────

@contextmanager
def get_conn():
    server   = os.environ["SQL_SERVER"]
    database = os.environ["SQL_DATABASE"]
    user     = os.environ["SQL_USER"]
    password = os.environ["SQL_PASSWORD"]
    driver   = os.getenv("DB_DRIVER", "{ODBC Driver 18 for SQL Server}")
    conn_str = (
        f"Driver={driver};"
        f"Server={server};"
        f"Database={database};"
        f"UID={user};"
        f"PWD={password};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=60;"
    )
    conn = pyodbc.connect(conn_str)
    try:
        yield conn
    finally:
        conn.close()


# ── User name data ─────────────────────────────────────────────────────────────

_FIRST_NAMES = [
    "James", "Oliver", "Noah", "Liam", "Ethan", "Mason", "Lucas", "Logan",
    "Jackson", "Aiden", "Carter", "Elijah", "Nathan", "Henry", "Owen",
    "Caleb", "Ryan", "Daniel", "Matthew", "Samuel", "Tyler", "Michael",
    "Jordan", "Blake", "Cameron",
    "Emma", "Sophia", "Olivia", "Ava", "Isabella", "Mia", "Charlotte",
    "Amelia", "Harper", "Evelyn", "Abigail", "Emily", "Ella", "Madison",
    "Scarlett", "Victoria", "Chloe", "Lily", "Grace", "Nora", "Zoe",
    "Hannah", "Layla", "Riley", "Aria",
]

_LAST_NAMES = [
    "Chen", "Rodriguez", "Kim", "Patel", "Williams", "Johnson", "Davis",
    "Wilson", "Anderson", "Taylor", "Martinez", "Thompson", "Garcia",
    "Jackson", "White", "Harris", "Martin", "Lee", "Walker", "Hall",
    "Allen", "Young", "Hernandez", "King", "Wright", "Lopez", "Hill",
    "Scott", "Green", "Adams", "Baker", "Gonzalez", "Nelson", "Carter",
    "Mitchell", "Perez", "Roberts", "Turner", "Phillips", "Campbell",
    "Parker", "Evans", "Edwards", "Collins", "Stewart", "Sanchez",
    "Morris", "Rogers", "Reed", "Flores",
]

# Each department: (name, n_employees_under_head)
# Total = 6 heads + (16+16+16+16+15+15) = 6 + 94 = 100
_DEPARTMENTS = [
    ("Engineering", 16),
    ("Sales",       16),
    ("Finance",     16),
    ("Operations",  16),
    ("HR",          15),
    ("Legal",       15),
]

_DEPT_ROLES = {
    "Engineering": ["Software Engineer", "Senior Engineer", "Lead Engineer",
                    "DevOps Engineer", "QA Engineer"],
    "Sales":       ["Account Executive", "Sales Representative", "Business Development",
                    "Sales Analyst", "Account Manager"],
    "Finance":     ["Financial Analyst", "Accountant", "Finance Manager",
                    "Budget Analyst", "Treasury Analyst"],
    "Operations":  ["Operations Analyst", "Process Manager", "Operations Coordinator",
                    "Project Manager", "Business Analyst"],
    "HR":          ["HR Business Partner", "Recruiter", "HR Analyst",
                    "Talent Specialist", "Benefits Coordinator"],
    "Legal":       ["Legal Counsel", "Compliance Analyst", "Contract Specialist",
                    "Paralegal", "Regulatory Analyst"],
}


def _generate_user_names(rng: random.Random) -> list[tuple[str, str]]:
    """Return 100 unique (first, last) pairs in deterministic order."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    attempts = 0
    while len(pairs) < 100 and attempts < 20_000:
        first = rng.choice(_FIRST_NAMES)
        last  = rng.choice(_LAST_NAMES)
        if (first, last) not in seen:
            seen.add((first, last))
            pairs.append((first, last))
        attempts += 1
    if len(pairs) < 100:
        raise RuntimeError(
            f"Could not generate 100 unique name combinations "
            f"(got {len(pairs)} after {attempts} attempts)"
        )
    return pairs


# ── Description text banks ────────────────────────────────────────────────────

_ORGANIC_AREAS = [
    "cloud infrastructure", "product delivery", "data engineering",
    "API design", "process improvement", "security hardening",
    "CI/CD automation", "stakeholder communication", "quality assurance",
    "cost optimisation", "cross-team collaboration", "customer success",
    "release management", "platform reliability", "documentation quality",
]
_ORGANIC_RESULTS = [
    "reducing cycle time by 30%",
    "cutting incident response time in half",
    "improving team velocity significantly",
    "enabling a successful quarterly launch",
    "eliminating a critical production risk",
    "saving the team weeks of rework",
    "delivering ahead of the original deadline",
    "improving customer satisfaction scores noticeably",
    "closing a longstanding technical gap",
    "unblocking multiple downstream teams",
]
_ORGANIC_TEMPLATES = [
    "{first} {last} consistently goes above and beyond in {area}, {result} for the organisation.",
    "{first} {last} demonstrated outstanding ownership this period, driving {area} work that directly contributed to {result}.",
    "I want to recognise {first} {last} for exceptional contribution to {area}. Their work was instrumental in {result}.",
    "{first} {last} stepped up during a critical phase of {area}, and their effort was central to {result}.",
    "Throughout this cycle, {first} {last} set a high bar in {area}. This directly led to {result}, benefiting the entire team.",
    "It is rare to see someone take ownership the way {first} {last} did in {area}. The outcome — {result} — speaks for itself.",
    "{first} {last} quietly and consistently raised the bar in {area} this period, ultimately {result}.",
]

def _organic_desc(first: str, last: str, rng: random.Random) -> str:
    area   = rng.choice(_ORGANIC_AREAS)
    result = rng.choice(_ORGANIC_RESULTS)
    tpl    = rng.choice(_ORGANIC_TEMPLATES)
    return tpl.format(first=first, last=last, area=area, result=result)


# Copy-paste templates: 4 families, each is a fixed block of text with {first}/{last}.
# Nominations using the same family will have near-identical descriptions — the
# copy-paste fraud detector (cosine similarity ≥ 0.92) will flag them.
_COPY_PASTE_FAMILIES = [
    (
        "{first} {last} consistently demonstrates exceptional dedication and has been instrumental "
        "in the success of our team deliverables. Their commitment to excellence and collaborative "
        "mindset makes them a standout contributor this period."
    ),
    (
        "I would like to formally recognise {first} {last} for their outstanding performance. "
        "They have consistently exceeded expectations, embodied our core values, and raised the "
        "quality bar across the board. Highly deserving of recognition."
    ),
    (
        "{first} {last} has made a significant positive impact through technical expertise and a "
        "collaborative approach. Their contributions have directly and measurably improved team "
        "outcomes and deserve formal acknowledgement."
    ),
    (
        "It is my pleasure to nominate {first} {last} for recognition this period. Their "
        "professionalism, attention to detail, and willingness to go beyond their role have made "
        "a tangible difference to our department's results."
    ),
]

def _copy_paste_desc(first: str, last: str, family_idx: int) -> str:
    return _COPY_PASTE_FAMILIES[family_idx % len(_COPY_PASTE_FAMILIES)].format(
        first=first, last=last
    )


# Transactional templates: self-referential language citing personal benefit to
# the nominator. The detector flags phrases like "helped me", "my project", etc.
_TRANSACTIONAL_TEMPLATES = [
    "{first} {last} helped me successfully deliver my quarterly goals. Without their direct support I would not have met my deadline.",
    "Thanks to {first} {last}'s assistance with my deliverable, I was able to hit my targets this period. I am personally grateful.",
    "{first} {last} stepped in to help me when I was behind on my project. Their direct involvement made the difference for me.",
    "I could not have completed my assignment without {first} {last}. They gave up their own time to help me meet my commitments.",
    "{first} {last} supported me personally during a very difficult sprint. My success this quarter is largely due to their help.",
    "My project would have failed without {first} {last}'s timely intervention. They helped me resolve a blocker that was entirely my own problem.",
    "{first} {last} agreed to take on extra work that I could not complete on time. Without them I would have missed my OKR.",
]

def _transactional_desc(first: str, last: str, rng: random.Random) -> str:
    return rng.choice(_TRANSACTIONAL_TEMPLATES).format(first=first, last=last)


# ── Date / status helpers ─────────────────────────────────────────────────────

def _rand_date(rng: random.Random,
               start: datetime = EPOCH_START,
               end:   datetime = EPOCH_END) -> datetime:
    delta = end - start
    return start + timedelta(seconds=rng.randint(0, int(delta.total_seconds())))


def _make_status(rng: random.Random,
                 paid_prob:   float = 0.35,
                 reject_prob: float = 0.05) -> str:
    r = rng.random()
    if r < paid_prob:                return "Paid"
    if r < paid_prob + reject_prob:  return "Rejected"
    return "Pending"


def _dates_for_status(
    nom_date: datetime,
    status:   str,
    rng:      random.Random,
) -> tuple[datetime | None, datetime | None]:
    if status == "Pending":
        return None, None
    approved = nom_date + timedelta(days=rng.randint(1, 14))
    if status == "Rejected":
        return approved, None
    paid = approved + timedelta(days=rng.randint(1, 10))
    return approved, paid


# ── Risk level helper ─────────────────────────────────────────────────────────

def _risk_level(score: int) -> str:
    if score >= 80: return "CRITICAL"
    if score >= 60: return "HIGH"
    if score >= 40: return "MEDIUM"
    if score >= 20: return "LOW"
    return "NONE"


# ── FindingHash — must match graph_pattern_detector._fingerprint() exactly ────

def _fingerprint(
    tenant_id:      int,
    pattern_type:   str,
    affected_users: list[int],   # must be sorted
    nomination_ids: list[int],   # must be sorted
) -> str:
    key = (
        f"{tenant_id}|{pattern_type}"
        f"|{json.dumps(affected_users)}"
        f"|{json.dumps(nomination_ids)}"
    )
    return hashlib.sha256(key.encode()).hexdigest()


# ── DB helpers ────────────────────────────────────────────────────────────────

def _insert_user(
    cur,
    first:      str,
    last:       str,
    title:      str,
    tenant_id:  int,
    manager_id: int | None,
) -> int:
    upn = f"{first.lower()}.{last.lower()}{DEMO_UPN_SUFFIX}"
    cur.execute(
        """
        INSERT INTO dbo.Users
               (userPrincipalName, userEmail, FirstName, LastName, Title, ManagerId, TenantId)
        OUTPUT INSERTED.UserId
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (upn, DEMO_EMAIL, first, last, title, manager_id, tenant_id),
    )
    return int(cur.fetchone()[0])


def _insert_nomination(
    cur,
    nominator_id:   int,
    beneficiary_id: int,
    approver_id:    int,
    amount:         int,
    desc:           str,
    nom_date:       datetime,
    status:         str,
    approved_date:  datetime | None,
    payed_date:     datetime | None,
    rng:            random.Random,
) -> int:
    notified = (nom_date + timedelta(seconds=rng.randint(5, 120))
                if status != "Pending" and rng.random() < 0.85
                else None)
    cur.execute(
        """
        INSERT INTO dbo.Nominations
               (NominatorId, BeneficiaryId, ApproverId, Amount,
                NominationDescription, NominationDate, Status,
                ApprovedDate, PayedDate, Currency, ApproverNotifiedAt)
        OUTPUT INSERTED.NominationId
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (nominator_id, beneficiary_id, approver_id, amount,
         desc, nom_date, status,
         approved_date, payed_date, CURRENCY, notified),
    )
    return int(cur.fetchone()[0])


# ── Tenant setup ──────────────────────────────────────────────────────────────

def get_or_create_tenant(conn, dry_run: bool) -> int:
    """
    Return the internal TenantId for the demo tenant, creating it if absent.
    DEMO_AAD_TENANT_ID is read from the environment (real UUID from Azure Portal).
    """
    aad_tenant_id = _graph_env()[0]   # validates it is set before doing anything

    cur = conn.cursor()
    cur.execute(
        "SELECT TenantId FROM dbo.Tenants WHERE TenantName = ?",
        (DEMO_TENANT_NAME,),
    )
    row = cur.fetchone()
    if row:
        tid = int(row[0])
        print(f"  Demo tenant already exists: TenantId={tid}")
        return tid
    if dry_run:
        print("  [dry-run] Would INSERT demo tenant → TenantId=<new>")
        return -1

    # Check which optional columns exist (migrations 0002, 0004)
    cur.execute(
        "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='Tenants' AND COLUMN_NAME='Config'"
    )
    has_config = cur.fetchone() is not None

    cur.execute(
        "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='Tenants' AND COLUMN_NAME='Domain'"
    )
    has_domain = cur.fetchone() is not None

    if has_config and has_domain:
        cur.execute(
            """
            INSERT INTO dbo.Tenants (TenantName, AzureAdTenantId, Config, Domain)
            OUTPUT INSERTED.TenantId
            VALUES (?, ?, ?, ?)
            """,
            (DEMO_TENANT_NAME, aad_tenant_id, DEMO_TENANT_CONFIG, DEMO_DOMAIN),
        )
    elif has_config:
        cur.execute(
            """
            INSERT INTO dbo.Tenants (TenantName, AzureAdTenantId, Config)
            OUTPUT INSERTED.TenantId
            VALUES (?, ?, ?)
            """,
            (DEMO_TENANT_NAME, aad_tenant_id, DEMO_TENANT_CONFIG),
        )
    else:
        cur.execute(
            """
            INSERT INTO dbo.Tenants (TenantName, AzureAdTenantId)
            OUTPUT INSERTED.TenantId
            VALUES (?, ?)
            """,
            (DEMO_TENANT_NAME, aad_tenant_id),
        )

    tid = int(cur.fetchone()[0])
    conn.commit()
    print(f"  Created demo tenant: TenantId={tid}")
    return tid


# ── Reset ─────────────────────────────────────────────────────────────────────

def reset_demo(conn, tenant_id: int, dry_run: bool) -> None:
    """
    Delete all demo data in FK-safe order:
      AAD      → delete 100 fictional user accounts from Entra
      DB       → findings → scores → nominations → users
    """
    cur = conn.cursor()

    # Collect UPNs before deleting (needed for AAD cleanup)
    cur.execute(
        "SELECT userPrincipalName FROM dbo.Users WHERE TenantId = ?",
        (tenant_id,),
    )
    demo_upns = [row[0] for row in cur.fetchall()]

    # Count before deleting
    cur.execute("SELECT COUNT(*) FROM dbo.GraphPatternFindings WHERE TenantId = ?", (tenant_id,))
    n_findings = int(cur.fetchone()[0])

    cur.execute(
        """
        SELECT COUNT(*) FROM dbo.FraudScores fs
        JOIN dbo.Nominations n ON fs.NominationId = n.NominationId
        WHERE n.NominatorId IN (SELECT UserId FROM dbo.Users WHERE TenantId = ?)
        """,
        (tenant_id,),
    )
    n_scores = int(cur.fetchone()[0])

    cur.execute(
        """
        SELECT COUNT(*) FROM dbo.Nominations
        WHERE NominatorId IN (SELECT UserId FROM dbo.Users WHERE TenantId = ?)
        """,
        (tenant_id,),
    )
    n_noms = int(cur.fetchone()[0])

    n_users = len(demo_upns)

    print(
        f"  Existing demo data: {n_findings} findings, {n_scores} scores, "
        f"{n_noms} nominations, {n_users} users"
    )

    if dry_run:
        print("  [dry-run] Would delete all of the above from DB and AAD.")
        return

    # ── AAD cleanup ───────────────────────────────────────────────────────────
    # Delete fictional accounts from Entra before removing DB rows
    # (keeps AAD + DB in sync even if the script is interrupted).
    print(f"  Deleting {n_users} AAD accounts...")
    token = _get_graph_token()
    deleted_aad = 0
    for upn in demo_upns:
        if delete_aad_user(token, upn):
            deleted_aad += 1
    print(f"  ✓ Deleted {deleted_aad} AAD accounts ({n_users - deleted_aad} already absent)")

    # ── DB cleanup (FK-safe order) ────────────────────────────────────────────

    # 1. FraudScores (FK on NominationId)
    cur.execute(
        """
        DELETE fs FROM dbo.FraudScores fs
        JOIN dbo.Nominations n ON fs.NominationId = n.NominationId
        WHERE n.NominatorId IN (SELECT UserId FROM dbo.Users WHERE TenantId = ?)
        """,
        (tenant_id,),
    )

    # 2. GraphPatternFindings
    cur.execute(
        "DELETE FROM dbo.GraphPatternFindings WHERE TenantId = ?",
        (tenant_id,),
    )

    # 3. ProcessedEvents (may reference demo nominations; optional FK)
    try:
        cur.execute(
            """
            DELETE pe FROM dbo.ProcessedEvents pe
            WHERE pe.NominationId IN (
                SELECT NominationId FROM dbo.Nominations
                WHERE NominatorId IN (SELECT UserId FROM dbo.Users WHERE TenantId = ?)
            )
            """,
            (tenant_id,),
        )
    except pyodbc.ProgrammingError:
        pass   # ProcessedEvents doesn't exist yet or NominationId column absent

    # 4. Nominations
    cur.execute(
        """
        DELETE FROM dbo.Nominations
        WHERE NominatorId IN (SELECT UserId FROM dbo.Users WHERE TenantId = ?)
        """,
        (tenant_id,),
    )

    # 5. Users
    cur.execute("DELETE FROM dbo.Users WHERE TenantId = ?", (tenant_id,))

    conn.commit()
    print(
        f"  ✓ Deleted {n_findings} findings, {n_scores} scores, "
        f"{n_noms} nominations, {n_users} users from DB"
    )


# ── Phase 2: Users ────────────────────────────────────────────────────────────

def seed_users(conn, tenant_id: int, rng: random.Random, dry_run: bool) -> dict:
    """
    Insert 100 fictional users.
    Returns a structured dict for use by the nomination seeder:
      {
        dept_name: {
          "head_id": int,
          "employee_ids": [int, ...],
        },
        ...
      }
    """
    print(f"\n[Phase 2] Seeding users (100 across 6 departments)...")
    name_pairs = _generate_user_names(rng)

    if dry_run:
        idx = 0
        for dept, n_emps in _DEPARTMENTS:
            first, last = name_pairs[idx]; idx += 1
            print(f"  Head  [{dept}]: {first} {last}  ({first.lower()}.{last.lower()}{DEMO_UPN_SUFFIX})")
            for _ in range(n_emps):
                f, l = name_pairs[idx]; idx += 1
                _ = (f, l)
        print(f"  [dry-run] Would create 100 AAD accounts + insert 100 Users rows")
        return {}

    # ── Step 1: Acquire Graph token once for all 100 accounts ────────────────
    print("  Acquiring Microsoft Graph token...")
    token = _get_graph_token()
    print("  ✓ Graph token acquired")

    cur = conn.cursor()
    result: dict[str, dict] = {}
    idx = 0
    aad_created = 0
    aad_existed = 0

    for dept, n_emps in _DEPARTMENTS:
        # ── Department head — no manager ──────────────────────────────────────
        first, last = name_pairs[idx]; idx += 1
        upn = f"{first.lower()}.{last.lower()}{DEMO_UPN_SUFFIX}"

        # Create in AAD (disabled account — impersonation target only)
        exists, _ = _aad_upn_exists(token, upn)
        create_aad_user(token, first, last, upn)
        if exists:
            aad_existed += 1
        else:
            aad_created += 1

        head_id = _insert_user(cur, first, last, f"{dept} Director", tenant_id, None)
        print(f"  Head  [{dept:12s}]: {first:10s} {last:12s}  UserId={head_id}  UPN={upn}")

        # ── Employees ─────────────────────────────────────────────────────────
        emp_ids: list[int] = []
        roles = _DEPT_ROLES[dept]
        for i in range(n_emps):
            first, last = name_pairs[idx]; idx += 1
            upn = f"{first.lower()}.{last.lower()}{DEMO_UPN_SUFFIX}"
            title = roles[i % len(roles)]

            exists, _ = _aad_upn_exists(token, upn)
            create_aad_user(token, first, last, upn)
            if exists:
                aad_existed += 1
            else:
                aad_created += 1

            eid = _insert_user(cur, first, last, title, tenant_id, head_id)
            emp_ids.append(eid)

        result[dept] = {"head_id": head_id, "employee_ids": emp_ids}

    conn.commit()
    total = sum(1 + len(v["employee_ids"]) for v in result.values())
    print(
        f"  ✓ AAD: {aad_created} created, {aad_existed} already existed"
    )
    print(f"  ✓ DB : {total} Users rows inserted")
    return result


# ── Phase 3: Nominations ──────────────────────────────────────────────────────

def seed_nominations(
    conn,
    tenant_id: int,
    dept_map:  dict,
    rng:       random.Random,
    dry_run:   bool,
) -> dict:
    """
    Insert 400 nominations.

    Pattern allocation
    ------------------
    Ring cycle noms   :  12  (3+4+5 directed edges)
    Ring camouflage   :  36  (~3 organic outsider noms per ring member)
    Approver affinity :  27  (15 + 12 noms in 2 clusters)
    Copy-paste        :  40  (8 clusters × 5 noms each)
    Transactional     :  20  (4 clusters × 5 noms each)
    Organic background: 265
    ─────────────────────────────────────────────────────
    Total             : 400

    Returns a tracking dict consumed by the fraud-score and findings seeders:
      {
        "ring_noms":         [[nom_ids_ring1], [nom_ids_ring2], [nom_ids_ring3]],
        "ring_user_ids":     [[user_ids_ring1], ...],
        "affinity_noms":     [[nom_ids_cluster1], [nom_ids_cluster2]],
        "affinity_user_ids": [[nominator_id, approver_id], ...],
        "copypaste_noms":    [[nom_ids_cluster0], ..., [nom_ids_cluster7]],
        "transact_noms":     [[nom_ids_cluster0], ..., [nom_ids_cluster3]],
        "organic_nom_ids":   [int, ...],
      }
    """
    print(f"\n[Phase 3] Seeding nominations (400 total)...")

    if dry_run:
        print(
            "  [dry-run] Would insert 400 nominations:\n"
            "    12 ring cycle edges + ~36 camouflage\n"
            "    27 approver-affinity  (2 clusters)\n"
            "    40 copy-paste         (8 clusters × 5)\n"
            "    20 transactional      (4 clusters × 5)\n"
            "   ~265 organic background"
        )
        return {
            "ring_noms":         [[], [], []],
            "ring_user_ids":     [[], [], []],
            "affinity_noms":     [[], []],
            "affinity_user_ids": [[], []],
            "copypaste_noms":    [[] for _ in range(8)],
            "transact_noms":     [[] for _ in range(4)],
            "organic_nom_ids":   [],
        }

    eng  = dept_map["Engineering"]
    sale = dept_map["Sales"]
    fin  = dept_map["Finance"]
    ops  = dept_map["Operations"]
    hr   = dept_map["HR"]
    leg  = dept_map["Legal"]

    # User pools
    # Ring participants are drawn from the first few employees of each dept
    ring1_users = eng["employee_ids"][:3]                        # Engineering 3-ring
    ring2_users = sale["employee_ids"][:4]                       # Sales 4-ring
    ring3_users = fin["employee_ids"][:5]                        # Finance 5-ring
    all_ring_users = set(ring1_users + ring2_users + ring3_users)

    # Affinity nominators: someone outside their "target" dept who consistently
    # nominates people from that dept (approved by that dept's head).
    affinity_nom1 = ops["employee_ids"][14]    # Ops employee → nominates Engineering team
    affinity_nom2 = leg["employee_ids"][14]    # Legal employee → nominates Sales team
    affinity_users = {affinity_nom1, affinity_nom2}

    # General pool — everyone not reserved for rings or affinity
    reserved = all_ring_users | affinity_users
    all_employee_ids: list[int] = []
    for dept_data in dept_map.values():
        all_employee_ids.extend(dept_data["employee_ids"])

    general_pool: list[int] = [uid for uid in all_employee_ids if uid not in reserved]

    # Name lookup for description generation
    # Build a reverse map from UserId → (first, last, manager_id)
    cur = conn.cursor()
    cur.execute(
        "SELECT UserId, FirstName, LastName, ManagerId FROM dbo.Users WHERE TenantId = ?",
        (tenant_id,),
    )
    user_info: dict[int, dict] = {
        row[0]: {"first": row[1], "last": row[2], "mgr": row[3]}
        for row in cur.fetchall()
    }

    def name(uid: int) -> tuple[str, str]:
        u = user_info[uid]
        return u["first"], u["last"]

    def mgr(uid: int) -> int:
        return user_info[uid]["mgr"]

    # Tracking
    tracking: dict = {
        "ring_noms":         [[], [], []],
        "ring_user_ids":     [ring1_users, ring2_users, ring3_users],
        "affinity_noms":     [[], []],
        "affinity_user_ids": [
            [affinity_nom1, eng["head_id"]],    # (nominator, approver they target)
            [affinity_nom2, sale["head_id"]],
        ],
        "copypaste_noms":    [[] for _ in range(8)],
        "transact_noms":     [[] for _ in range(4)],
        "organic_nom_ids":   [],
    }

    total_inserted = 0

    if dry_run:
        print("  [dry-run] Would insert 400 nominations")
        return tracking

    # ── 1. Ring nominations (directed cycles) ─────────────────────────────────
    print("  [1/6] Ring nominations (12 cycle edges + 36 camouflage)...")
    rings = [ring1_users, ring2_users, ring3_users]
    outsider_pool = [uid for uid in general_pool]   # ring members nominate outsiders too

    for ring_idx, ring in enumerate(rings):
        ring_set = set(ring)
        # Directed cycle: A→B→C→...→A
        for i, nom_id in enumerate(ring):
            ben_id = ring[(i + 1) % len(ring)]
            apr_id = mgr(ben_id)
            amount = rng.randint(400, 1_500)
            f, l   = name(ben_id)
            desc   = _organic_desc(f, l, rng)     # organic-looking to blend in
            ndate  = _rand_date(rng)
            status = _make_status(rng, paid_prob=0.55)
            ad, pd = _dates_for_status(ndate, status, rng)
            nid = _insert_nomination(cur, nom_id, ben_id, apr_id, amount,
                                     desc, ndate, status, ad, pd, rng)
            tracking["ring_noms"][ring_idx].append(nid)
            total_inserted += 1

        # Camouflage: ~3 organic noms from each ring member to outsiders
        # These are NOT in tracking["ring_noms"] so Phase 4 gives them LOW scores.
        outsiders = [uid for uid in outsider_pool if uid not in ring_set]
        for nom_id in ring:
            for _ in range(rng.randint(2, 4)):
                ben_id = rng.choice(outsiders)
                apr_id = mgr(ben_id)
                amount = rng.randint(200, 1_200)
                f, l   = name(ben_id)
                desc   = _organic_desc(f, l, rng)
                ndate  = _rand_date(rng)
                status = _make_status(rng)
                ad, pd = _dates_for_status(ndate, status, rng)
                _insert_nomination(cur, nom_id, ben_id, apr_id, amount,
                                   desc, ndate, status, ad, pd, rng)
                total_inserted += 1

    conn.commit()
    # Note: camouflage IDs aren't tracked precisely — that's intentional.
    # We only need ring cycle IDs for the GraphPatternFindings fingerprint.

    # ── 2. Approver-affinity nominations ─────────────────────────────────────
    print("  [2/6] Approver-affinity nominations (27 total, 2 clusters)...")
    affinity_targets = [
        (affinity_nom1, eng["employee_ids"][3:],   15),   # targets in Eng
        (affinity_nom2, sale["employee_ids"][4:],  12),   # targets in Sales
    ]
    for cluster_idx, (nom_id, target_pool, n) in enumerate(affinity_targets):
        for _ in range(n):
            ben_id = rng.choice([uid for uid in target_pool if uid != nom_id])
            apr_id = mgr(ben_id)
            amount = rng.randint(600, 3_000)
            f, l   = name(ben_id)
            desc   = _organic_desc(f, l, rng)
            ndate  = _rand_date(rng)
            status = _make_status(rng, paid_prob=0.82, reject_prob=0.02)
            ad, pd = _dates_for_status(ndate, status, rng)
            nid = _insert_nomination(cur, nom_id, ben_id, apr_id, amount,
                                     desc, ndate, status, ad, pd, rng)
            tracking["affinity_noms"][cluster_idx].append(nid)
            total_inserted += 1

    conn.commit()

    # ── 3. Copy-paste nominations (8 clusters × 5 noms) ──────────────────────
    print("  [3/6] Copy-paste nominations (40 total, 8 clusters)...")
    # Cluster families: distribute 8 clusters across 4 template families
    family_map = [0, 0, 1, 1, 2, 2, 3, 3]   # cluster_idx → family_idx
    cp_pool = [uid for uid in general_pool]

    for cluster_idx in range(8):
        family = family_map[cluster_idx]
        for _ in range(5):
            nom_id = rng.choice(cp_pool)
            ben_candidates = [uid for uid in cp_pool if uid != nom_id]
            ben_id = rng.choice(ben_candidates)
            apr_id = mgr(ben_id)
            amount = rng.randint(350, 1_800)
            f, l   = name(ben_id)
            desc   = _copy_paste_desc(f, l, family)    # same template → near-identical
            ndate  = _rand_date(rng)
            status = _make_status(rng, paid_prob=0.28, reject_prob=0.12)
            ad, pd = _dates_for_status(ndate, status, rng)
            nid = _insert_nomination(cur, nom_id, ben_id, apr_id, amount,
                                     desc, ndate, status, ad, pd, rng)
            tracking["copypaste_noms"][cluster_idx].append(nid)
            total_inserted += 1

    conn.commit()

    # ── 4. Transactional-language nominations (4 clusters × 5 noms) ──────────
    print("  [4/6] Transactional-language nominations (20 total, 4 clusters)...")
    for cluster_idx in range(4):
        for _ in range(5):
            nom_id = rng.choice(general_pool)
            ben_candidates = [uid for uid in general_pool if uid != nom_id]
            ben_id = rng.choice(ben_candidates)
            apr_id = mgr(ben_id)
            amount = rng.randint(200, 900)
            f, l   = name(ben_id)
            desc   = _transactional_desc(f, l, rng)
            ndate  = _rand_date(rng)
            status = _make_status(rng, paid_prob=0.30, reject_prob=0.14)
            ad, pd = _dates_for_status(ndate, status, rng)
            nid = _insert_nomination(cur, nom_id, ben_id, apr_id, amount,
                                     desc, ndate, status, ad, pd, rng)
            tracking["transact_noms"][cluster_idx].append(nid)
            total_inserted += 1

    conn.commit()

    # ── 5. Organic background (remaining to reach 400) ────────────────────────
    n_organic = 400 - total_inserted
    print(f"  [5/6] Organic background nominations ({n_organic} to reach 400)...")
    all_organic_nominators = general_pool
    all_organic_bens       = general_pool

    for _ in range(n_organic):
        nom_id = rng.choice(all_organic_nominators)
        ben_id = rng.choice([uid for uid in all_organic_bens if uid != nom_id])
        apr_id = mgr(ben_id)
        amount = rng.randint(100, 2_000)
        f, l   = name(ben_id)
        desc   = _organic_desc(f, l, rng)
        ndate  = _rand_date(rng)
        status = _make_status(rng)
        ad, pd = _dates_for_status(ndate, status, rng)
        nid = _insert_nomination(cur, nom_id, ben_id, apr_id, amount,
                                 desc, ndate, status, ad, pd, rng)
        tracking["organic_nom_ids"].append(nid)
        total_inserted += 1

    conn.commit()
    print(f"  ✓ Inserted {total_inserted} nominations")
    return tracking


# ── Phase 4: FraudScores ──────────────────────────────────────────────────────

def seed_fraud_scores(
    conn,
    tenant_id: int,
    tracking:  dict,
    rng:       random.Random,
    dry_run:   bool,
) -> None:
    """
    Insert FraudScores for every nomination in the demo tenant.

    Score ranges by pattern:
      Ring cycle noms    → CRITICAL  85–97
      Affinity noms      → HIGH      62–82
      Copy-paste noms    → HIGH      60–78
      Transactional noms → MEDIUM    38–58
      Organic / camoufl  → NONE/LOW   0–30
    """
    print(f"\n[Phase 4] Seeding fraud scores...")

    if dry_run:
        print("  [dry-run] Would upsert FraudScores for all 400 nominations")
        return

    cur = conn.cursor()

    # Collect all nomination IDs for this tenant
    cur.execute(
        """
        SELECT n.NominationId
        FROM dbo.Nominations n
        WHERE n.NominatorId IN (SELECT UserId FROM dbo.Users WHERE TenantId = ?)
        """,
        (tenant_id,),
    )
    all_nom_ids = {int(row[0]) for row in cur.fetchall()}

    # Build score-override sets
    ring_ids      = {nid for ring in tracking["ring_noms"] for nid in ring}
    affinity_ids  = {nid for cl in tracking["affinity_noms"] for nid in cl}
    cp_ids        = {nid for cl in tracking["copypaste_noms"] for nid in cl}
    transact_ids  = {nid for cl in tracking["transact_noms"] for nid in cl}

    rows: list[tuple] = []
    for nid in all_nom_ids:
        if nid in ring_ids:
            score = rng.randint(85, 97)
            flags = "Nomination ring detected, Circular pattern"
        elif nid in affinity_ids:
            score = rng.randint(62, 82)
            flags = "Approver affinity, Concentrated beneficiary pool"
        elif nid in cp_ids:
            score = rng.randint(60, 78)
            flags = "Copy-paste description, Low originality"
        elif nid in transact_ids:
            score = rng.randint(38, 58)
            flags = "Transactional language, Personal benefit framing"
        else:
            score = rng.randint(0, 30)
            flags = ""

        level = _risk_level(score)
        rows.append((nid, score, level, flags or None))

    # Batch upsert using MERGE — safe to re-run
    sql = """
        MERGE dbo.FraudScores AS target
        USING (SELECT ? AS NominationId) AS source ON target.NominationId = source.NominationId
        WHEN MATCHED THEN
            UPDATE SET FraudScore = ?, RiskLevel = ?, FraudFlags = ?
        WHEN NOT MATCHED THEN
            INSERT (NominationId, FraudScore, RiskLevel, FraudFlags)
            VALUES (?,            ?,          ?,         ?);
    """
    for nid, score, level, flags in rows:
        cur.execute(sql, (nid, score, level, flags, nid, score, level, flags))

    conn.commit()
    high_risk = sum(1 for _, score, _, _ in rows if score >= 60)
    print(f"  ✓ Upserted {len(rows)} fraud scores ({high_risk} HIGH/CRITICAL)")


# ── Phase 5: GraphPatternFindings ─────────────────────────────────────────────

def seed_graph_findings(
    conn,
    tenant_id: int,
    tracking:  dict,
    rng:       random.Random,
    dry_run:   bool,
) -> None:
    """
    Insert pre-seeded GraphPatternFindings so the Integrity tab shows realistic
    results without waiting for the Monday fraud-analytics-job run.

    Findings:
      3 × Ring              (one per ring size)
      2 × ApproverAffinity  (one per cluster)
      8 × CopyPaste         (one per cluster)
      4 × TransactionalLanguage (one per cluster)
    Total: 17 findings
    """
    print(f"\n[Phase 5] Seeding graph pattern findings (17 total)...")

    if dry_run:
        print("  [dry-run] Would insert 17 GraphPatternFindings")
        return

    run_id    = str(uuid.uuid4())
    detected  = datetime.now(timezone.utc)
    findings: list[tuple] = []

    table = "dbo.GraphPatternFindings"

    # Retrieve name map for detail strings
    cur = conn.cursor()
    cur.execute(
        "SELECT UserId, FirstName, LastName FROM dbo.Users WHERE TenantId = ?",
        (tenant_id,),
    )
    names = {row[0]: f"{row[1]} {row[2]}" for row in cur.fetchall()}

    def _finding(
        pattern_type:   str,
        severity:       str,
        affected_users: list[int],
        nomination_ids: list[int],
        detail:         str,
        total_amount:   int,
    ) -> tuple:
        au = sorted(affected_users)
        ni = sorted(nomination_ids)
        return (
            tenant_id,
            pattern_type,
            severity,
            json.dumps(au),
            json.dumps(ni),
            detail[:1000],
            detected,
            run_id,
            _fingerprint(tenant_id, pattern_type, au, ni),
            total_amount,
        )

    # ── Ring findings ─────────────────────────────────────────────────────────
    ring_labels = [
        ("3-person", "Critical"),
        ("4-person", "Critical"),
        ("5-person", "Critical"),
    ]
    for ring_idx, (label, severity) in enumerate(ring_labels):
        ring_users = tracking["ring_user_ids"][ring_idx]
        ring_nids  = tracking["ring_noms"][ring_idx]
        user_names = " → ".join(names.get(uid, str(uid)) for uid in ring_users)
        detail = (
            f"{label} directed nomination ring detected. "
            f"Members: {user_names}. "
            f"Each member nominates the next in a closed cycle, consistent with "
            f"coordinated reciprocal recognition."
        )
        # Look up total amount
        cur.execute(
            f"SELECT ISNULL(SUM(Amount),0) FROM dbo.Nominations WHERE NominationId IN "
            f"({','.join('?' for _ in ring_nids)})",
            ring_nids,
        )
        total_amount = int(cur.fetchone()[0])
        findings.append(_finding("Ring", severity, ring_users, ring_nids, detail, total_amount))

    # ── Approver-affinity findings ────────────────────────────────────────────
    affinity_labels = ["Engineering team", "Sales team"]
    for cluster_idx in range(2):
        nom_id, apr_id = tracking["affinity_user_ids"][cluster_idx]
        nom_nids       = tracking["affinity_noms"][cluster_idx]
        approval_rate  = round(rng.uniform(0.80, 0.92), 2)
        detail = (
            f"{names.get(nom_id, str(nom_id))} submitted {len(nom_nids)} nominations "
            f"targeting the {affinity_labels[cluster_idx]} exclusively. "
            f"Approval rate: {int(approval_rate*100)}% vs ~40% baseline. "
            f"Approver: {names.get(apr_id, str(apr_id))}."
        )
        cur.execute(
            f"SELECT ISNULL(SUM(Amount),0) FROM dbo.Nominations WHERE NominationId IN "
            f"({','.join('?' for _ in nom_nids)})",
            nom_nids,
        )
        total_amount = int(cur.fetchone()[0])
        findings.append(_finding(
            "ApproverAffinity", "High",
            [nom_id, apr_id], nom_nids, detail, total_amount,
        ))

    # ── Copy-paste findings ───────────────────────────────────────────────────
    family_names = [
        "Template A — dedication & deliverables",
        "Template A — dedication & deliverables",
        "Template B — exceeded expectations",
        "Template B — exceeded expectations",
        "Template C — technical expertise",
        "Template C — technical expertise",
        "Template D — professionalism & detail",
        "Template D — professionalism & detail",
    ]
    for cluster_idx in range(8):
        cp_nids = tracking["copypaste_noms"][cluster_idx]
        detail = (
            f"Copy-paste cluster ({family_names[cluster_idx]}): "
            f"{len(cp_nids)} nominations share near-identical description text "
            f"(cosine similarity ≥ 0.92). Only beneficiary names differ."
        )
        # Collect unique users involved
        cur.execute(
            f"SELECT NominatorId, BeneficiaryId FROM dbo.Nominations WHERE NominationId IN "
            f"({','.join('?' for _ in cp_nids)})",
            cp_nids,
        )
        cp_users = list({uid for row in cur.fetchall() for uid in row})
        cur.execute(
            f"SELECT ISNULL(SUM(Amount),0) FROM dbo.Nominations WHERE NominationId IN "
            f"({','.join('?' for _ in cp_nids)})",
            cp_nids,
        )
        total_amount = int(cur.fetchone()[0])
        findings.append(_finding(
            "CopyPaste", "High",
            cp_users, cp_nids, detail, total_amount,
        ))

    # ── Transactional-language findings ──────────────────────────────────────
    for cluster_idx in range(4):
        tl_nids = tracking["transact_noms"][cluster_idx]
        detail = (
            f"Transactional language cluster {cluster_idx + 1}: "
            f"{len(tl_nids)} nominations contain personal-benefit framing "
            f"('helped me', 'my project', 'my deliverable'). "
            f"Nominations may reflect personal reciprocity rather than merit."
        )
        cur.execute(
            f"SELECT NominatorId, BeneficiaryId FROM dbo.Nominations WHERE NominationId IN "
            f"({','.join('?' for _ in tl_nids)})",
            tl_nids,
        )
        tl_users = list({uid for row in cur.fetchall() for uid in row})
        cur.execute(
            f"SELECT ISNULL(SUM(Amount),0) FROM dbo.Nominations WHERE NominationId IN "
            f"({','.join('?' for _ in tl_nids)})",
            tl_nids,
        )
        total_amount = int(cur.fetchone()[0])
        findings.append(_finding(
            "TransactionalLanguage", "Medium",
            tl_users, tl_nids, detail, total_amount,
        ))

    # ── Insert all findings ───────────────────────────────────────────────────
    # Load existing hashes to avoid duplicates (same idempotency as the detector)
    cur.execute(
        f"SELECT FindingHash FROM {table} WHERE TenantId = ? AND FindingHash IS NOT NULL",
        (tenant_id,),
    )
    existing_hashes = {row[0] for row in cur.fetchall()}

    sql = f"""
        INSERT INTO {table}
               (TenantId, PatternType, Severity,
                AffectedUsers, NominationIds, Detail, DetectedAt, RunId, FindingHash,
                TotalAmount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    inserted = 0
    skipped  = 0
    for f in findings:
        fhash = f[8]   # FindingHash is index 8
        if fhash in existing_hashes:
            skipped += 1
            continue
        cur.execute(sql, f)
        inserted += 1

    conn.commit()
    print(f"  ✓ Inserted {inserted} findings ({skipped} skipped as duplicates)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Terian Services Demo tenant")
    parser.add_argument("--reset",   action="store_true",
                        help="Delete all existing demo data before re-seeding")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan only — no DB writes")
    args = parser.parse_args()
    dry_run = args.dry_run

    print("=" * 60)
    print("  Demo Tenant Seeder")
    print(f"  Mode: {'DRY RUN — no DB writes' if dry_run else 'LIVE'}")
    print("=" * 60)

    rng = random.Random(42)   # fixed seed → fully reproducible

    with get_conn() as conn:
        # ── Phase 1: Tenant ───────────────────────────────────────────────────
        print(f"\n[Phase 1] Tenant setup...")
        tenant_id = get_or_create_tenant(conn, dry_run)

        if args.reset and tenant_id != -1:
            print(f"\n[Reset] Deleting existing demo data (TenantId={tenant_id})...")
            reset_demo(conn, tenant_id, dry_run)
            if not dry_run:
                # Re-create tenant row after wipe (reset deletes users, not the tenant)
                print(f"\n[Phase 1] Tenant preserved (TenantId={tenant_id})")

        # ── Phase 2: Users ────────────────────────────────────────────────────
        if dry_run or tenant_id != -1:
            # Check if users already exist (post-reset they won't)
            if not dry_run:
                chk = conn.cursor()
                chk.execute(
                    "SELECT COUNT(*) FROM dbo.Users WHERE TenantId = ?",
                    (tenant_id,),
                )
                existing_users = int(chk.fetchone()[0])
                chk.close()
                if existing_users > 0 and not args.reset:
                    print(f"\n[Phase 2] {existing_users} users already exist — skipping user seeding.")
                    print("  (run with --reset to re-seed from scratch)")
                    return

            dept_map = seed_users(conn, tenant_id, rng, dry_run)

            # ── Phase 3: Nominations ──────────────────────────────────────────
            tracking = seed_nominations(conn, tenant_id, dept_map, rng, dry_run)

            # ── Phase 4: FraudScores ──────────────────────────────────────────
            seed_fraud_scores(conn, tenant_id, tracking, rng, dry_run)

            # ── Phase 5: GraphPatternFindings ─────────────────────────────────
            seed_graph_findings(conn, tenant_id, tracking, rng, dry_run)

    print("\n" + "=" * 60)
    if dry_run:
        print("  DRY RUN complete — no changes made to AAD or DB.")
    else:
        print("  ✓ Demo tenant seeding complete.")
        print(f"  Tenant      : {DEMO_TENANT_NAME}")
        print(f"  App domain  : {DEMO_DOMAIN}")
        print(f"  UPN domain  : {DEMO_UPN_SUFFIX.lstrip('@')}")
        print(f"  Notifications → {DEMO_EMAIL}")
        print()
        print("  Next steps:")
        print("  1. Assign yourself AWard_Nomination_Admin in the Demo AAD tenant")
        print(f"     Enterprise Apps → Award Nomination → Users and groups")
        print("  2. Verify demo-awards.terian-services.com DNS + SWA custom domain")
        print("  3. Log in at demo-awards.terian-services.com and test impersonation")
    print("=" * 60)


if __name__ == "__main__":
    main()
