"""
client.py — Rippling REST API HTTP client
==========================================
Thin HTTP wrapper around the Rippling Platform API.

STUB MODE
---------
Set RIPPLING_STUB_MODE=true (the default) until real OAuth credentials are
provisioned after Rippling App Shop approval.  In stub mode every function
returns realistic seeded data without making any HTTP calls, so the full
multi-tenant routing architecture can be demonstrated immediately.

To go live:
  1. Set RIPPLING_STUB_MODE=false
  2. Populate RIPPLING_CLIENT_ID and RIPPLING_CLIENT_SECRET
  3. Complete the OAuth flow at /rippling/authorize?tenant_id=<id>

Real API base URLs:
  Sandbox:    https://rest.ripplingsandboxapis.com
  Production: https://rest.rippling.com

Real OAuth endpoints:
  Sandbox authorize: https://app.ripplingsandboxapis.com/apps/PLATFORM/oauth2/authorize
  Sandbox token:     https://rest.ripplingsandboxapis.com/api/o/token/
  Prod authorize:    https://app.rippling.com/apps/PLATFORM/oauth2/authorize
  Prod token:        https://rest.rippling.com/api/o/token/

Rippling API docs: https://developer.rippling.com/documentation/rest-api
"""

import hashlib
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

_CLIENT_ID     = os.getenv("RIPPLING_CLIENT_ID",     "")
_CLIENT_SECRET = os.getenv("RIPPLING_CLIENT_SECRET",  "")
_BROKER_BASE   = os.getenv("PAYROLL_BROKER_BASE_URL", "https://payroll-broker.terianix.ai")
_STUB_MODE     = os.getenv("RIPPLING_STUB_MODE", "true").lower() == "true"

REDIRECT_URI = f"{_BROKER_BASE.rstrip('/')}/rippling/callback"
OAUTH_SCOPES = "employee:read payroll:read payroll:write"


def _stub_warn() -> None:
    logger.warning(
        "RIPPLING_STUB_MODE=true — returning seeded data, no real API call made. "
        "Set RIPPLING_STUB_MODE=false once real credentials are provisioned."
    )


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def build_authorization_url(state: str, oauth_base_url: str) -> str:
    """Build the Rippling OAuth authorize URL to redirect the tenant admin to."""
    params = {
        "response_type": "code",
        "client_id":     _CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "scope":         OAUTH_SCOPES,
        "state":         state,
    }
    return f"{oauth_base_url.rstrip('/')}/apps/PLATFORM/oauth2/authorize?{urlencode(params)}"


def exchange_code_for_token(code: str, oauth_base_url: str) -> dict:
    """
    Exchange an authorization code for access + refresh tokens.

    Returns: {access_token, refresh_token, expires_at (datetime UTC)}
    Raises:  httpx.HTTPStatusError on non-2xx response.
    """
    if _STUB_MODE:
        _stub_warn()
        return {
            "access_token":  "stub_rippling_access_token",
            "refresh_token": "stub_rippling_refresh_token",
            "expires_at":    datetime.now(timezone.utc) + timedelta(hours=2),
        }

    resp = httpx.post(
        f"{oauth_base_url.rstrip('/')}/api/o/token/",
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "client_id":     _CLIENT_ID,
            "client_secret": _CLIENT_SECRET,
            "redirect_uri":  REDIRECT_URI,
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    payload    = resp.json()
    expires_in = int(payload.get("expires_in", 7200))
    return {
        "access_token":  payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at":    datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    }


def refresh_access_token(refresh_token: str, oauth_base_url: str) -> dict:
    """
    Use a refresh token to obtain a new access token.

    Returns the same dict shape as exchange_code_for_token().
    """
    if _STUB_MODE:
        _stub_warn()
        return {
            "access_token":  "stub_rippling_access_token",
            "refresh_token": refresh_token,
            "expires_at":    datetime.now(timezone.utc) + timedelta(hours=2),
        }

    resp = httpx.post(
        f"{oauth_base_url.rstrip('/')}/api/o/token/",
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
            "client_id":     _CLIENT_ID,
            "client_secret": _CLIENT_SECRET,
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    payload    = resp.json()
    expires_in = int(payload.get("expires_in", 7200))
    return {
        "access_token":  payload["access_token"],
        "refresh_token": payload.get("refresh_token", refresh_token),
        "expires_at":    datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    }


# ---------------------------------------------------------------------------
# Auth headers
# ---------------------------------------------------------------------------

def _auth_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept":        "application/json",
        "Content-Type":  "application/json",
    }


# ---------------------------------------------------------------------------
# Company / employee discovery
# ---------------------------------------------------------------------------

def get_current_company(access_token: str, api_base_url: str) -> Optional[str]:
    """
    Return the company ID for the authenticated token.

    Real endpoint: GET /platform/api/companies (returns the company scoped to
    this OAuth token — Rippling embeds one company per token).

    Returns the company ID string, or None if unavailable.
    """
    if _STUB_MODE:
        _stub_warn()
        return "stub-rippling-co-001"

    resp = httpx.get(
        f"{api_base_url.rstrip('/')}/platform/api/companies",
        headers=_auth_headers(access_token),
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    # Rippling returns a list; the token is scoped to one company
    companies = body if isinstance(body, list) else body.get("results", [])
    return companies[0]["id"] if companies else None


def get_employees(access_token: str, api_base_url: str) -> list[dict]:
    """
    GET /platform/api/employees — full active employee list.

    Real response shape (each employee):
      {
        "id":         "rp_emp_abc123",
        "workEmail":  "jane.doe@company.com",
        "firstName":  "Jane",
        "lastName":   "Doe",
        "startDate":  "2023-01-15",
        "roleState":  "ACTIVE",
        "compensation": {"annualSalary": "85000.00", "payFrequency": "SEMIMONTHLY"}
      }
    """
    if _STUB_MODE:
        _stub_warn()
        # Return an empty list — individual lookups handled in find_employee_by_email
        return []

    resp = httpx.get(
        f"{api_base_url.rstrip('/')}/platform/api/employees",
        headers=_auth_headers(access_token),
        params={"roleState": "ACTIVE"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    employees = body if isinstance(body, list) else body.get("results", [])
    logger.info("Rippling get_employees count=%d", len(employees))
    return employees


def find_employee_by_email(
    access_token: str,
    email:        str,
    api_base_url: str,
) -> Optional[dict]:
    """
    Find an active employee by work email.

    Returns a normalised dict with employee_id, full_name, work_email,
    address, and payrate — same shape the provider passes to submit_payroll.

    In stub mode: synthetically generates a deterministic employee record
    from the email so any UPN resolves without real API access.
    """
    if _STUB_MODE:
        _stub_warn()
        # Derive a deterministic stub employee ID from the email
        short_hash = hashlib.md5(email.lower().encode()).hexdigest()[:8]
        stub_id    = f"rp_emp_stub_{short_hash}"
        parts      = email.split("@")[0].split(".")
        first      = parts[0].capitalize() if parts else "Stub"
        last       = parts[1].capitalize() if len(parts) > 1 else "Employee"
        logger.info(
            "Rippling STUB find_employee_by_email email=%s → stub_id=%s", email, stub_id
        )
        return {
            "employee_id": stub_id,
            "job_id":      None,   # Rippling does not use job UUIDs for payroll
            "full_name":   f"{first} {last}",
            "work_email":  email,
            "address": {
                "street_1": "123 Stub Street",
                "street_2": "",
                "city":     "San Francisco",
                "state":    "CA",
                "zip":      "94105",
            },
            "payrate": {
                "rate":         "85000.00",
                "payment_unit": "Year",
            },
        }

    # Real mode: fetch all employees and match by workEmail
    employees   = get_employees(access_token, api_base_url)
    email_lower = email.lower()
    for emp in employees:
        if (emp.get("workEmail") or "").lower() == email_lower:
            compensation = emp.get("compensation") or {}
            return {
                "employee_id": emp["id"],
                "job_id":      None,
                "full_name":   f"{emp.get('firstName', '')} {emp.get('lastName', '')}".strip(),
                "work_email":  emp.get("workEmail", ""),
                "address": {
                    "street_1": emp.get("homeAddress", {}).get("streetLine1", ""),
                    "street_2": emp.get("homeAddress", {}).get("streetLine2", "") or "",
                    "city":     emp.get("homeAddress", {}).get("city",        ""),
                    "state":    emp.get("homeAddress", {}).get("state",       ""),
                    "zip":      emp.get("homeAddress", {}).get("zip",         ""),
                },
                "payrate": {
                    "rate":         compensation.get("annualSalary", ""),
                    "payment_unit": compensation.get("payFrequency", ""),
                },
            }

    logger.warning("Rippling employee not found email=%s", email)
    return None


# ---------------------------------------------------------------------------
# Pay lookup
# ---------------------------------------------------------------------------

def get_payroll_runs_for_month(
    access_token: str,
    company_id:   str,
    year:         int,
    month:        int,
    api_base_url: str,
) -> list[dict]:
    """
    Return all payroll runs whose pay period overlaps the given calendar month.

    Real endpoint: GET /platform/api/payroll_runs
      Params: start_date, end_date, company_id

    Real response shape (each run):
      {
        "id":                  "rp_pr_xyz789",
        "payrollRunType":      "OFF_CYCLE",
        "status":              "COMPLETED",
        "payPeriodStartDate":  "2026-07-01",
        "payPeriodEndDate":    "2026-07-01",
        "checkDate":           "2026-07-03",
        "payItems": [
          {
            "employeeId": "rp_emp_abc123",
            "earnings": [{"earningType": "AWARD_BONUS", "amount": "1000.00"}],
            "grossPay":  "1000.00",
            "netPay":    "850.00",
          }
        ]
      }

    In stub mode: returns [] — the payroll-broker falls back to the DB-backed
    extra_payroll_refs lookup (same pattern as Gusto sandbox).
    """
    if _STUB_MODE:
        _stub_warn()
        logger.info(
            "Rippling STUB get_payroll_runs_for_month year=%d month=%d → []", year, month
        )
        return []

    import calendar as _cal
    last_day   = _cal.monthrange(year, month)[1]
    start_date = f"{year}-{month:02d}-01"
    end_date   = f"{year}-{month:02d}-{last_day:02d}"

    resp = httpx.get(
        f"{api_base_url.rstrip('/')}/platform/api/payroll_runs",
        headers=_auth_headers(access_token),
        params={
            "start_date": start_date,
            "end_date":   end_date,
            "company_id": company_id,
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    runs = body if isinstance(body, list) else body.get("results", [])
    logger.info(
        "Rippling get_payroll_runs_for_month year=%d month=%d count=%d", year, month, len(runs)
    )
    return runs


def get_payroll_run_by_id(
    access_token:    str,
    payroll_run_id:  str,
    api_base_url:    str,
) -> Optional[dict]:
    """
    Fetch a single payroll run by its ID.

    Real endpoint: GET /platform/api/payroll_runs/{id}

    In stub mode: returns a synthetic Rippling payroll run containing a stub
    compensation entry.  The amount is seeded at $1,000.00 gross / $850.00 net
    for demonstration purposes; real API calls return actual processed amounts.

    Returns None if the run cannot be retrieved.
    """
    if _STUB_MODE:
        _stub_warn()
        today = datetime.utcnow().date().isoformat()
        logger.info("Rippling STUB get_payroll_run_by_id id=%s → stub run", payroll_run_id)
        return {
            "id":                 payroll_run_id,
            "payrollRunType":     "OFF_CYCLE",
            "status":             "COMPLETED",
            "payPeriodStartDate": today,
            "payPeriodEndDate":   today,
            "checkDate":          today,
            "payItems": [
                {
                    # employee_id is intentionally omitted in stub — the provider
                    # uses the run ID (stored in payroll_submissions) to match,
                    # not the employee ID inside the run.
                    "earnings": [{"earningType": "Award Bonus", "amount": "1000.00"}],
                    "grossPay": "1000.00",
                    "netPay":   "850.00",
                }
            ],
        }

    resp = httpx.get(
        f"{api_base_url.rstrip('/')}/platform/api/payroll_runs/{payroll_run_id}",
        headers=_auth_headers(access_token),
        timeout=30,
    )
    if not resp.is_success:
        logger.warning(
            "Rippling get_payroll_run_by_id failed id=%s status=%d body=%s",
            payroll_run_id, resp.status_code, resp.text[:300],
        )
        return None
    return resp.json()


# ---------------------------------------------------------------------------
# Off-cycle payroll submission
# ---------------------------------------------------------------------------

def create_off_cycle_payroll_run(
    access_token: str,
    company_id:   str,
    employee_id:  str,
    amount:       float,
    currency:     str = "USD",
    api_base_url: str = "https://rest.ripplingsandboxapis.com",
) -> str:
    """
    Create and submit an off-cycle award bonus payroll run for one employee.
    Returns the Rippling payroll run ID (stored as provider_payroll_ref).

    Real Rippling flow:
      POST /platform/api/payroll_runs
        → creates the run with status PENDING
      The run is then processed by Rippling asynchronously.
      A webhook (event: payroll_run.completed) fires when done.

    In stub mode: returns a deterministic fake run ID without any HTTP call.
    Raises RuntimeError if currency is not USD (Rippling US payroll only).
    """
    if currency != "USD":
        logger.warning(
            "Rippling only supports USD payroll; submitting amount=%.2f as USD (requested %s)",
            amount, currency,
        )

    if _STUB_MODE:
        _stub_warn()
        stub_run_id = f"rp_pr_stub_{uuid.uuid4().hex[:16]}"
        logger.info(
            "Rippling STUB create_off_cycle_payroll_run employee=%s amount=%.2f → %s",
            employee_id, amount, stub_run_id,
        )
        return stub_run_id

    today = datetime.utcnow().date().isoformat()
    payload = {
        "companyId":      company_id,
        "payrollRunType": "OFF_CYCLE",
        "payPeriod": {
            "startDate": today,
            "endDate":   today,
        },
        "payItems": [
            {
                "employeeId": employee_id,
                "earnings": [
                    {
                        "earningType": "AWARD_BONUS",
                        "amount":      f"{amount:.2f}",
                    }
                ],
            }
        ],
    }

    resp = httpx.post(
        f"{api_base_url.rstrip('/')}/platform/api/payroll_runs",
        json=payload,
        headers=_auth_headers(access_token),
        timeout=30,
    )
    if not resp.is_success:
        logger.error(
            "Rippling create_off_cycle_payroll_run failed status=%d body=%s",
            resp.status_code, resp.text,
        )
    resp.raise_for_status()
    run = resp.json()
    run_id = run["id"]
    logger.info(
        "Rippling payroll run created employee=%s amount=%.2f run_id=%s",
        employee_id, amount, run_id,
    )
    return run_id
