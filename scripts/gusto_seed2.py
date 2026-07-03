"""
gusto_seed2.py
--------------
Bulk payroll onboarding for all 291 Gusto sandbox employees in tenant 1.
Reads employee UUIDs from gusto_seed_results.csv (produced by gusto_seed.py).

For each employee (1-based index n in CSV order):
  - SSN:          12345{n:04d}  (123450001, 123450002, …, 123450291)
  - Home address: {n} Main Street, Austin TX 78701
  - Work address: shared Austin TX company location (created once, reused)
  - Federal taxes (W-4), state taxes (TX), payment method (Check)

Already-completed employees (onboarding_status == onboarding_completed) are skipped.
Results written to gusto_seed2_results.csv (one row per employee, written as we go).

Usage:
    set GUSTO_ACCESS_TOKEN=<token>
    set GUSTO_COMPANY_UUID=<company uuid>
    python gusto_seed2.py
"""

import csv
import json
import os
import sys
import time
from pathlib import Path

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

PACE       = 0.4   # seconds between requests (avoid rate limits)
SCRIPT_DIR = Path(__file__).parent
INPUT_CSV  = SCRIPT_DIR / "gusto_seed_results.csv"
OUTPUT_CSV = SCRIPT_DIR / "gusto_seed2_results.csv"

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
        print(f"    body: {resp.text[:400]}")
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw": resp.text}


def _put(client: httpx.Client, url: str, payload: dict):
    resp = client.put(url, json=payload, headers=HEADERS)
    if resp.status_code >= 400:
        print(f"    PUT {resp.status_code} {url}")
        print(f"    body: {resp.text[:400]}")
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw": resp.text}


def _post(client: httpx.Client, url: str, payload: dict):
    resp = client.post(url, json=payload, headers=HEADERS)
    if resp.status_code >= 400:
        print(f"    POST {resp.status_code} {url}")
        print(f"    body: {resp.text[:400]}")
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {"raw": resp.text}


# ── Company work-location helper ───────────────────────────────────────────────

def get_or_create_tx_location(client: httpx.Client) -> str | None:
    """Return the UUID of the Austin TX company location, creating it if needed."""
    status, body = _get(client, f"{API_BASE}/v1/companies/{COMPANY_UUID}/locations")
    time.sleep(PACE)
    if status != 200:
        print(f"  Could not list company locations ({status})")
        return None

    locations = body if isinstance(body, list) else []
    tx = next(
        (loc for loc in locations
         if loc.get("state") == "TX" and loc.get("city", "").lower() == "austin"),
        None,
    )
    if tx:
        loc_uuid = tx.get("uuid") or tx.get("id")
        print(f"  Using existing Austin TX location: {loc_uuid}")
        return loc_uuid

    status, body = _post(
        client,
        f"{API_BASE}/v1/companies/{COMPANY_UUID}/locations",
        WORK_LOCATION,
    )
    time.sleep(PACE)
    if status not in (200, 201):
        print(f"  FAILED to create Austin TX location ({status})")
        return None
    loc_uuid = body.get("uuid") or body.get("id")
    print(f"  Created Austin TX location: {loc_uuid}")
    return loc_uuid


# ── Onboarding status check ────────────────────────────────────────────────────

def is_onboarding_complete(client: httpx.Client, employee_uuid: str) -> bool:
    status, body = _get(client, f"{API_BASE}/v1/employees/{employee_uuid}/onboarding_status")
    time.sleep(PACE)
    if status != 200:
        return False
    return body.get("onboarding_status") == "onboarding_completed"


# ── Per-employee onboarding ────────────────────────────────────────────────────

def onboard_employee(
    client: httpx.Client,
    n: int,
    email: str,
    employee_uuid: str,
    loc_uuid: str,
) -> tuple[str, str]:
    """
    Run all onboarding steps for one employee.
    Returns (status, notes) where status is 'completed' | 'failed' | 'skipped'.
    """
    ssn          = f"12345{n:04d}"
    home_street  = f"{n} Main Street"

    # ── Skip if already complete ───────────────────────────────────────────────
    if is_onboarding_complete(client, employee_uuid):
        return "skipped", "onboarding_completed already"

    # ── Fetch employee record (need version token) ─────────────────────────────
    status, emp_body = _get(client, f"{API_BASE}/v1/employees/{employee_uuid}")
    time.sleep(PACE)
    if status != 200:
        return "failed", f"could not fetch employee ({status})"
    emp_version = emp_body.get("version", "")

    # ── Step 1: SSN ───────────────────────────────────────────────────────────
    status, _ = _put(
        client,
        f"{API_BASE}/v1/employees/{employee_uuid}",
        {"version": emp_version, "ssn": ssn},
    )
    time.sleep(PACE)
    if status not in (200, 201):
        return "failed", f"SSN PUT failed ({status})"

    # ── Step 1a: Home address ─────────────────────────────────────────────────
    status, _ = _post(
        client,
        f"{API_BASE}/v1/employees/{employee_uuid}/home_addresses",
        {
            "street_1":       home_street,
            "city":           "Austin",
            "state":          "TX",
            "zip":            "78701",
            "country":        "US",
            "effective_date": "2023-01-01",
        },
    )
    time.sleep(PACE)
    if status not in (200, 201):
        print(f"    home_address FAILED ({status}) — continuing")

    # ── Step 1b: Work address → location UUID ─────────────────────────────────
    status, _ = _post(
        client,
        f"{API_BASE}/v1/employees/{employee_uuid}/work_addresses",
        {"location_uuid": loc_uuid, "effective_date": "2023-01-01"},
    )
    time.sleep(PACE)
    if status not in (200, 201):
        print(f"    work_address FAILED ({status}) — continuing")

    # Update job location_id
    status, jobs_body = _get(client, f"{API_BASE}/v1/employees/{employee_uuid}/jobs")
    time.sleep(PACE)
    jobs = jobs_body if isinstance(jobs_body, list) else []
    if jobs:
        job         = jobs[0]
        job_uuid    = job.get("uuid") or job.get("id")
        job_version = job.get("version", "")
        _put(
            client,
            f"{API_BASE}/v1/jobs/{job_uuid}",
            {"version": job_version, "location_id": loc_uuid},
        )
        time.sleep(PACE)

    # ── Step 2: Federal taxes (W-4) ───────────────────────────────────────────
    status, fed_body = _get(client, f"{API_BASE}/v1/employees/{employee_uuid}/federal_taxes")
    time.sleep(PACE)
    if status != 200:
        return "failed", f"federal_taxes GET failed ({status})"

    status, _ = _put(
        client,
        f"{API_BASE}/v1/employees/{employee_uuid}/federal_taxes",
        {
            "version":           fed_body.get("version", ""),
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
    if status not in (200, 201):
        return "failed", f"federal_taxes PUT failed ({status})"

    # ── Step 2b: State taxes (TX) ─────────────────────────────────────────────
    status, st_body = _get(client, f"{API_BASE}/v1/employees/{employee_uuid}/state_taxes")
    time.sleep(PACE)
    if status == 200:
        tx_records = st_body if isinstance(st_body, list) else []
        tx = next((r for r in tx_records if r.get("state") == "TX"), None)
        if tx:
            questions = tx.get("questions", [])
            answers   = [{"key": q["key"], "value": q.get("default", "false")} for q in questions]
            _put(
                client,
                f"{API_BASE}/v1/employees/{employee_uuid}/state_taxes",
                {"version": tx.get("version", ""), "state": "TX", "answers": answers},
            )
            time.sleep(PACE)

    # ── Step 3: Payment method ────────────────────────────────────────────────
    status, pm_body = _get(client, f"{API_BASE}/v1/employees/{employee_uuid}/payment_method")
    time.sleep(PACE)
    if status != 200:
        return "failed", f"payment_method GET failed ({status})"

    status, _ = _put(
        client,
        f"{API_BASE}/v1/employees/{employee_uuid}/payment_method",
        {"version": pm_body.get("version", ""), "type": "Check"},
    )
    time.sleep(PACE)
    if status not in (200, 201):
        return "failed", f"payment_method PUT failed ({status})"

    return "completed", f"ssn={ssn} address={home_street}"


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    # Load input CSV
    if not INPUT_CSV.exists():
        sys.exit(f"ERROR: {INPUT_CSV} not found — run gusto_seed.py first.")

    rows = []
    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("employee_uuid"):
                rows.append(row)

    total = len(rows)
    print(f"Loaded {total} employees from {INPUT_CSV.name}")

    with httpx.Client(timeout=30) as client:
        # Resolve work location once
        print("\nResolving Austin TX work location...")
        loc_uuid = get_or_create_tx_location(client)
        if not loc_uuid:
            sys.exit("ERROR: Cannot proceed without a company work location.")

        # Open output CSV
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as out_f:
            writer = csv.DictWriter(out_f, fieldnames=["n", "email", "employee_uuid", "ssn", "status", "notes"])
            writer.writeheader()

            completed = skipped = failed = 0

            for n, row in enumerate(rows, start=1):
                email         = row["email"]
                employee_uuid = row["employee_uuid"]
                ssn           = f"12345{n:04d}"

                print(f"\n[{n}/{total}] {email}")
                result_status, notes = onboard_employee(client, n, email, employee_uuid, loc_uuid)

                writer.writerow({
                    "n":             n,
                    "email":         email,
                    "employee_uuid": employee_uuid,
                    "ssn":           ssn,
                    "status":        result_status,
                    "notes":         notes,
                })
                out_f.flush()

                mark = "✓" if result_status == "completed" else ("–" if result_status == "skipped" else "✗")
                print(f"  {mark} {result_status}: {notes}")

                if result_status == "completed":
                    completed += 1
                elif result_status == "skipped":
                    skipped += 1
                else:
                    failed += 1

    print(f"\n{'─' * 60}")
    print(f"Done. {completed} completed, {skipped} skipped, {failed} failed.")
    print(f"Results: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
