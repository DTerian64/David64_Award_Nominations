"""
gusto_seed2.py
--------------
Completes payroll onboarding for specific Gusto sandbox employees so they
become eligible to receive off-cycle payroll.

gusto_seed.py only did 3 steps (create employee → job → compensation).
This script adds the remaining steps Gusto requires for payroll eligibility:

  0. Onboarding status   — GET /v1/employees/{uuid}/onboarding_status (diagnostic)
  1. SSN                 — PUT /v1/employees/{uuid}  (SSN only)
  1a.Home address        — POST /v1/employees/{uuid}/home_addresses (effective_date)
  1b.Work address        — POST /v1/employees/{uuid}/work_addresses (effective_date)
                           + GET/POST /v1/companies/{uuid}/locations (Austin TX)
                           + GET /v1/employees/{uuid}/jobs → PUT /v1/jobs/{job_id} location_id
  2. Federal tax (W-4)   — GET version → PUT /v1/employees/{uuid}/federal_taxes
  2b.State taxes (TX)    — GET version → PUT /v1/employees/{uuid}/state_taxes
  3. Payment method      — PUT /v1/employees/{uuid}/payment_method
  4. Onboarding status   — re-check after all steps

All data is fake but structurally valid for sandbox testing.

Target employees:
  Alex Moore   — Alex.Moore@terian-services.com
  Jesse Taylor — Jesse.Taylor@terian-services.com

Usage:
    set GUSTO_ACCESS_TOKEN=<your token>
    set GUSTO_COMPANY_UUID=<your company uuid>
    python gusto_seed2.py
"""

import json
import os
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────
ACCESS_TOKEN = os.environ.get("GUSTO_ACCESS_TOKEN")
COMPANY_UUID = os.environ.get("GUSTO_COMPANY_UUID")
API_BASE     = os.environ.get("GUSTO_API_BASE", "https://api.gusto-demo.com")

if not ACCESS_TOKEN or not COMPANY_UUID:
    sys.exit("ERROR: GUSTO_ACCESS_TOKEN and GUSTO_COMPANY_UUID must be set.")

HEADERS = {
    "Authorization":       f"Bearer {ACCESS_TOKEN}",
    "Content-Type":        "application/json",
    "X-Gusto-API-Version": "2026-06-15",
}

PACE = 0.4  # seconds between requests (avoid rate limits)

# ── Target employees with fake-but-valid sandbox SSNs ─────────────────────────
TARGETS = [
    {"email": "Alex.Moore@terian-services.com",   "ssn": "123450001"},
    {"email": "Jesse.Taylor@terian-services.com", "ssn": "123450002"},
]

# Fake US address — Austin TX (no state income tax → simplest state tax setup)
FAKE_ADDRESS = {
    "street_1": "123 Main Street",
    "city":     "Austin",
    "state":    "TX",
    "zip":      "78701",
    "country":  "US",
}

# Work location to create/find for the company (Sandbox Inc.)
WORK_LOCATION = {
    "phone_number": "5125550100",
    "street_1":     "123 Main Street",
    "city":         "Austin",
    "state":        "TX",
    "zip":          "78701",
    "country":      "US",
}


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _get(client: httpx.Client, url: str, params: dict | None = None):
    resp = client.get(url, headers=HEADERS, params=params or {})
    if resp.status_code >= 400:
        print(f"    GET {resp.status_code} {url}")
        print(f"    body: {resp.text[:600]}")
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw": resp.text}


def _put(client: httpx.Client, url: str, payload: dict):
    resp = client.put(url, json=payload, headers=HEADERS)
    if resp.status_code >= 400:
        print(f"    PUT {resp.status_code} {url}")
        print(f"    body: {resp.text[:600]}")
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw": resp.text}


def _post(client: httpx.Client, url: str, payload: dict):
    resp = client.post(url, json=payload, headers=HEADERS)
    if resp.status_code >= 400:
        print(f"    POST {resp.status_code} {url}")
        print(f"    body: {resp.text[:600]}")
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw": resp.text}


# ── Company work-location helper ──────────────────────────────────────────────

def get_or_create_tx_location(client: httpx.Client) -> str | None:
    """
    Return the UUID of an Austin TX company location, creating it if needed.
    Gusto requires a work location to be linked to each employee job.
    """
    status, body = _get(client, f"{API_BASE}/v1/companies/{COMPANY_UUID}/locations")
    time.sleep(PACE)
    if status != 200:
        print(f"    Could not list company locations ({status})")
        return None

    locations = body if isinstance(body, list) else []
    # Look for an existing Austin TX location
    tx = next(
        (loc for loc in locations
         if loc.get("state") == "TX" and loc.get("city", "").lower() == "austin"),
        None,
    )
    if tx:
        loc_uuid = tx.get("uuid") or tx.get("id")
        print(f"       Found existing Austin TX location: {loc_uuid}")
        return loc_uuid

    # Create it
    status, body = _post(
        client,
        f"{API_BASE}/v1/companies/{COMPANY_UUID}/locations",
        WORK_LOCATION,
    )
    time.sleep(PACE)
    if status not in (200, 201):
        print(f"       FAILED to create Austin TX location ({status})")
        return None
    loc_uuid = body.get("uuid") or body.get("id")
    print(f"       Created Austin TX location: {loc_uuid}")
    return loc_uuid


# ── Onboarding status helper ───────────────────────────────────────────────────

def print_onboarding_status(client: httpx.Client, employee_uuid: str, label: str = "") -> None:
    """Fetch and print Gusto's onboarding status for the employee."""
    status, body = _get(client, f"{API_BASE}/v1/employees/{employee_uuid}/onboarding_status")
    time.sleep(PACE)
    prefix = f"  [{label}] " if label else "  "
    if status != 200:
        print(f"{prefix}Could not fetch onboarding status ({status})")
        return

    onboarding_status = body.get("onboarding_status", "unknown")
    steps             = body.get("onboarding_steps", [])
    print(f"{prefix}Onboarding status: {onboarding_status}")
    if steps:
        for step in steps:
            name      = step.get("id") or step.get("title") or str(step)
            completed = step.get("completed", False)
            required  = step.get("required", True)
            mark      = "✓" if completed else ("✗" if required else "○")
            print(f"  {prefix}  {mark} {name}")
    else:
        print(f"{prefix}  (no step details returned)")
        print(f"{prefix}  raw: {json.dumps(body, indent=4)[:800]}")


# ── Employee lookup ────────────────────────────────────────────────────────────

def find_employee(client: httpx.Client, email: str) -> str | None:
    """Return employee_uuid for the given email, or None."""
    status, body = _get(client, f"{API_BASE}/v1/companies/{COMPANY_UUID}/employees")
    time.sleep(PACE)
    if status != 200 or not isinstance(body, list):
        print(f"    Could not list employees: {status}")
        return None

    email_lower = email.lower()
    for emp in body:
        if email_lower in (
            (emp.get("work_email") or "").lower(),
            (emp.get("email")      or "").lower(),
        ):
            return emp["uuid"]
    return None


# ── Main ───────────────────────────────────────────────────────────────────────

with httpx.Client(timeout=30) as client:
    for target in TARGETS:
        email = target["email"]
        ssn   = target["ssn"]
        print(f"\n── {email} {'─' * (60 - len(email))}")

        # ── Find employee ─────────────────────────────────────────────────────
        employee_uuid = find_employee(client, email)
        if not employee_uuid:
            print(f"  FAILED: employee not found in Gusto — skipping")
            continue
        print(f"  Found employee: {employee_uuid}")

        # ── Step 0: Onboarding status (before) ───────────────────────────────
        print("  [0] Onboarding status (before):")
        print_onboarding_status(client, employee_uuid, label="before")

        # Fetch full employee record to get current version token
        status, emp_body = _get(client, f"{API_BASE}/v1/employees/{employee_uuid}")
        time.sleep(PACE)
        if status != 200:
            print(f"  FAILED: could not fetch employee record ({status}) — skipping")
            continue
        emp_version = emp_body.get("version", "")

        # ── Step 1: SSN only ──────────────────────────────────────────────────
        print("  [1/4] Setting SSN...")
        status, body = _put(
            client,
            f"{API_BASE}/v1/employees/{employee_uuid}",
            {
                "version": emp_version,
                "ssn":     ssn,
            },
        )
        time.sleep(PACE)
        if status in (200, 201):
            print(f"       ✓ SSN={ssn}")
        else:
            print(f"       FAILED ({status}) — skipping this employee")
            continue

        # ── Step 1a: Home address (sub-endpoint, API 2026-06-15) ─────────────
        print("  [1a/4] Setting home address...")
        status, body = _post(
            client,
            f"{API_BASE}/v1/employees/{employee_uuid}/home_addresses",
            {
                **FAKE_ADDRESS,
                "effective_date": "2023-01-01",
            },
        )
        time.sleep(PACE)
        if status in (200, 201):
            print(f"       ✓ Home address: Austin TX 78701")
        else:
            print(f"       FAILED ({status}) — continuing anyway")

        # ── Step 1b: Work address (location_uuid ref) + company location → job ─
        print("  [1b/4] Setting work address...")
        # Get/create the company location first — its UUID is what work_addresses expects
        loc_uuid = get_or_create_tx_location(client)
        if not loc_uuid:
            print("         Could not obtain TX location UUID — skipping work address")
        else:
            # POST work_address referencing the company location UUID
            status, body = _post(
                client,
                f"{API_BASE}/v1/employees/{employee_uuid}/work_addresses",
                {
                    "location_uuid":  loc_uuid,
                    "effective_date": "2023-01-01",
                },
            )
            time.sleep(PACE)
            if status in (200, 201):
                print(f"       ✓ Work address → location {loc_uuid} (Austin TX)")
            else:
                print(f"       FAILED work_addresses ({status}) — continuing anyway")

            # Also assign the location to the job (for payroll computation)
            status, jobs_body = _get(client, f"{API_BASE}/v1/employees/{employee_uuid}/jobs")
            time.sleep(PACE)
            jobs = jobs_body if isinstance(jobs_body, list) else []
            if not jobs:
                print(f"         No jobs found for employee ({status}) — skipping work address")
            else:
                job = jobs[0]
                job_uuid    = job.get("uuid") or job.get("id")
                job_version = job.get("version", "")
                status, body = _put(
                    client,
                    f"{API_BASE}/v1/jobs/{job_uuid}",
                    {
                        "version":     job_version,
                        "location_id": loc_uuid,
                    },
                )
                time.sleep(PACE)
                if status in (200, 201):
                    print(f"         ✓ Job {job_uuid} → location {loc_uuid} (Austin TX)")
                else:
                    print(f"         FAILED to update job location ({status})")

        # ── Step 2: Federal tax withholding (W-4) ─────────────────────────────
        print("  [2/4] Setting federal taxes (W-4)...")
        status, fed_body = _get(client, f"{API_BASE}/v1/employees/{employee_uuid}/federal_taxes")
        time.sleep(PACE)
        if status != 200:
            print(f"       FAILED to fetch federal taxes ({status}) — skipping")
            continue
        fed_version = fed_body.get("version", "")

        status, body = _put(
            client,
            f"{API_BASE}/v1/employees/{employee_uuid}/federal_taxes",
            {
                "version":           fed_version,
                "filing_status":     "Single",
                "extra_withholding": "0.00",
                "two_jobs":          False,
                "dependents_amount": "0.00",
                "other_income":      "0.00",
                "deductions":        "0.00",
                "w4_data_type":      "rev_2020_w4",
            },
        )
        time.sleep(PACE)
        if status in (200, 201):
            print(f"       ✓ W-4: Single, no extras")
        else:
            print(f"       FAILED ({status}) — skipping this employee")
            continue

        # ── Step 2b: State taxes (Texas) ──────────────────────────────────────
        print("  [2b/4] Setting state taxes (TX)...")
        status, st_body = _get(client, f"{API_BASE}/v1/employees/{employee_uuid}/state_taxes")
        time.sleep(PACE)
        if status != 200:
            print(f"       Could not fetch state taxes ({status}) — printing raw and continuing")
            print(f"       raw: {st_body}")
        else:
            # Gusto returns a list of state tax records (one per state)
            print(f"       State tax records returned: {json.dumps(st_body, indent=4)[:800]}")

            # Find the TX record and PUT it if a version is available
            tx_records = st_body if isinstance(st_body, list) else []
            tx = next((r for r in tx_records if r.get("state") == "TX"), None)

            if tx:
                tx_version = tx.get("version", "")
                questions  = tx.get("questions", [])

                # Build answers: set all boolean questions to False, leave others blank
                answers = []
                for q in questions:
                    answers.append({
                        "key":   q["key"],
                        "value": q.get("default", "false"),
                    })

                status, body = _put(
                    client,
                    f"{API_BASE}/v1/employees/{employee_uuid}/state_taxes",
                    {
                        "version":  tx_version,
                        "state":    "TX",
                        "answers":  answers,
                    },
                )
                time.sleep(PACE)
                if status in (200, 201):
                    print(f"       ✓ TX state taxes set")
                else:
                    print(f"       FAILED ({status}) — continuing anyway")
            else:
                print(f"       No TX record in response — continuing")

        # ── Step 3: Payment method ────────────────────────────────────────────
        print("  [3/4] Setting payment method to Check...")
        status, pm_body = _get(client, f"{API_BASE}/v1/employees/{employee_uuid}/payment_method")
        time.sleep(PACE)
        if status != 200:
            print(f"       FAILED to fetch payment method ({status}) — skipping")
            continue
        pm_version = pm_body.get("version", "")

        status, body = _put(
            client,
            f"{API_BASE}/v1/employees/{employee_uuid}/payment_method",
            {"version": pm_version, "type": "Check"},
        )
        time.sleep(PACE)
        if status in (200, 201):
            print(f"       ✓ Payment method: Check")
        else:
            print(f"       FAILED ({status}) — see body above")
            continue

        # ── Step 4: Onboarding status (after) ────────────────────────────────
        print("  [4/4] Onboarding status (after):")
        print_onboarding_status(client, employee_uuid, label="after")

print("\nDone.")
