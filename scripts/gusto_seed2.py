"""
gusto_seed2.py
--------------
Completes payroll onboarding for specific Gusto sandbox employees so they
become eligible to receive off-cycle payroll.

gusto_seed.py only did 3 steps (create employee → job → compensation).
This script adds the remaining 3 steps Gusto requires for payroll eligibility:

  1. SSN + home address  — PUT /v1/employees/{uuid}
  2. Federal tax (W-4)   — GET version → PUT /v1/employees/{uuid}/federal_taxes
  3. Payment method      — PUT /v1/employees/{uuid}/payment_method

All data is fake but structurally valid for sandbox testing.

Target employees:
  Alex Moore   — Alex.Moore@terian-services.com
  Jesse Taylor — Jesse.Taylor@terian-services.com

Usage:
    set GUSTO_ACCESS_TOKEN=<your token>
    set GUSTO_COMPANY_UUID=<your company uuid>
    python gusto_seed2.py
"""

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
    "country":  "USA",
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


# ── Employee lookup ────────────────────────────────────────────────────────────

def find_employee(client: httpx.Client, email: str) -> tuple[str | None, str | None]:
    """
    Return (employee_uuid, version) for the given email, or (None, None).
    Checks both work_email and personal email fields.
    """
    status, body = _get(
        client,
        f"{API_BASE}/v1/companies/{COMPANY_UUID}/employees",
    )
    time.sleep(PACE)
    if status != 200 or not isinstance(body, list):
        print(f"    Could not list employees: {status}")
        return None, None

    email_lower = email.lower()
    for emp in body:
        if email_lower in (
            (emp.get("work_email") or "").lower(),
            (emp.get("email")      or "").lower(),
        ):
            return emp["uuid"], emp.get("version", "")
    return None, None


# ── Main ───────────────────────────────────────────────────────────────────────

with httpx.Client(timeout=30) as client:
    for target in TARGETS:
        email = target["email"]
        ssn   = target["ssn"]
        print(f"\n── {email} {'─' * (60 - len(email))}")

        # ── Find employee ─────────────────────────────────────────────────────
        employee_uuid, _ = find_employee(client, email)
        if not employee_uuid:
            print(f"  FAILED: employee not found in Gusto — skipping")
            continue
        print(f"  Found employee: {employee_uuid}")

        # Fetch full employee record to get current version token
        status, emp_body = _get(client, f"{API_BASE}/v1/employees/{employee_uuid}")
        time.sleep(PACE)
        if status != 200:
            print(f"  FAILED: could not fetch employee record ({status}) — skipping")
            continue
        emp_version = emp_body.get("version", "")

        # ── Step 1: SSN + home address ────────────────────────────────────────
        print("  [1/3] Setting SSN + home address...")
        status, body = _put(
            client,
            f"{API_BASE}/v1/employees/{employee_uuid}",
            {
                "version":      emp_version,
                "ssn":          ssn,
                "home_address": FAKE_ADDRESS,
            },
        )
        time.sleep(PACE)
        if status in (200, 201):
            print(f"       ✓ SSN={ssn}  address=Austin TX 78701")
        else:
            print(f"       FAILED ({status}) — skipping this employee")
            continue

        # ── Step 2: Federal tax withholding (W-4) ─────────────────────────────
        print("  [2/3] Setting federal taxes (W-4)...")
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

        # ── Step 3: Payment method ────────────────────────────────────────────
        print("  [3/3] Setting payment method to Check...")
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

        print(f"  ✓ {email} is now payroll-eligible")

print("\nDone.")
