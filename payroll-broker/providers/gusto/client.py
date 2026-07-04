"""
client.py — Gusto Embedded API HTTP client
==========================================
Thin HTTP wrapper around the Gusto v1 API.  No business logic lives here —
all decisions about when/how to call these functions belong in provider.py.

API and OAuth base URLs are passed as function arguments (sourced from the
payroll_providers row), so they are per-provider-instance rather than
hardcoded to a single environment.

Environment variables (application-level, not per-provider):
    GUSTO_CLIENT_ID
    GUSTO_CLIENT_SECRET
    PAYROLL_BROKER_BASE_URL e.g. https://payroll-broker.terianix.ai
                            (used to build the redirect_uri)
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

_CLIENT_ID     = os.getenv("GUSTO_CLIENT_ID",        "")
_CLIENT_SECRET = os.getenv("GUSTO_CLIENT_SECRET",    "")
_BROKER_BASE   = os.getenv("PAYROLL_BROKER_BASE_URL", "https://payroll-broker.terianix.ai")

REDIRECT_URI = f"{_BROKER_BASE.rstrip('/')}/gusto/callback"
OAUTH_SCOPES = "openid employees:read:sensitive payrolls:run companies:read"


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def build_authorization_url(state: str, oauth_base_url: str) -> str:
    """Build the Gusto OAuth authorize URL to redirect the tenant admin to."""
    params = {
        "response_type": "code",
        "client_id":     _CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "scope":         OAUTH_SCOPES,
        "state":         state,
    }
    return f"{oauth_base_url.rstrip('/')}/oauth/authorize?{urlencode(params)}"


def exchange_code_for_token(code: str, oauth_base_url: str) -> dict:
    """
    Exchange an authorization code for access + refresh tokens.

    Returns: {access_token, refresh_token, expires_at (datetime UTC)}
    Raises:  httpx.HTTPStatusError on non-2xx response.
    """
    resp = httpx.post(
        f"{oauth_base_url.rstrip('/')}/oauth/token",
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
    Raises httpx.HTTPStatusError on failure.
    """
    resp = httpx.post(
        f"{oauth_base_url.rstrip('/')}/oauth/token",
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
# Company / employee discovery
# ---------------------------------------------------------------------------

def _auth_headers(access_token: str) -> dict:
    return {
        "Authorization":       f"Bearer {access_token}",
        "Accept":              "application/json",
        "Content-Type":        "application/json",
        "X-Gusto-API-Version": "2026-06-15",
    }


def get_current_user(access_token: str, api_base_url: str) -> dict:
    """GET /v1/me — returns the authenticated user's profile and companies."""
    resp = httpx.get(
        f"{api_base_url.rstrip('/')}/v1/me",
        headers=_auth_headers(access_token),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_company_uuid_from_me(access_token: str, api_base_url: str) -> Optional[str]:
    """
    Return the first company UUID from /v1/me, or None if none exist.
    Gusto's embedded model scopes each OAuth token to one company.
    """
    me = get_current_user(access_token, api_base_url)
    companies = me.get("roles", {}).get("payroll_admin", {}).get("companies", [])
    return companies[0].get("uuid") if companies else None


def get_employees(access_token: str, company_uuid: str, api_base_url: str) -> list[dict]:
    """GET /v1/companies/{uuid}/employees — full employee list for the company."""
    resp = httpx.get(
        f"{api_base_url.rstrip('/')}/v1/companies/{company_uuid}/employees",
        headers=_auth_headers(access_token),
        params={"include": "all_compensations"},
        timeout=30,
    )
    resp.raise_for_status()
    employees = resp.json()
    logger.info("Gusto get_employees company=%s count=%d", company_uuid, len(employees))
    return employees


def _extract_payrate(emp: dict) -> dict:
    """Return {rate, payment_unit} from the employee's first job's most recent compensation."""
    jobs = emp.get("jobs", [])
    if not jobs:
        return {"rate": "", "payment_unit": ""}
    compensations = jobs[0].get("compensations", [])
    if not compensations:
        return {"rate": "", "payment_unit": ""}
    comp = compensations[-1]   # most recent
    return {
        "rate":         comp.get("rate", ""),
        "payment_unit": comp.get("payment_unit", ""),
    }


def _extract_address(emp: dict) -> dict:
    """Return flattened home_address fields."""
    addr = emp.get("home_address") or {}
    return {
        "street_1": addr.get("street_1", ""),
        "street_2": addr.get("street_2", "") or "",
        "city":     addr.get("city",     ""),
        "state":    addr.get("state",    ""),
        "zip":      addr.get("zip",      ""),
    }


def find_employee_by_email(
    access_token:  str,
    company_uuid:  str,
    email:         str,
    api_base_url:  str,
) -> Optional[dict]:
    """
    Search the company's employees for one matching the given email.

    Returns a dict with employee_id, job_id, and full profile fields, or None
    if not found. Checks both work_email and personal email fields.
    """
    employees   = get_employees(access_token, company_uuid, api_base_url)
    email_lower = email.lower()

    for emp in employees:
        if email_lower in (
            (emp.get("work_email") or "").lower(),
            (emp.get("email")      or "").lower(),
        ):
            jobs   = emp.get("jobs", [])
            job_id = jobs[0]["uuid"] if jobs else None
            return {
                "employee_id":  emp["uuid"],
                "job_id":       job_id,
                # Profile fields
                "full_name":    f"{emp.get('first_name', '')} {emp.get('last_name', '')}".strip(),
                "work_email":   emp.get("work_email") or emp.get("email", ""),
                "address":      _extract_address(emp),
                "payrate":      _extract_payrate(emp),
            }

    logger.warning("Gusto employee not found email=%s company=%s", email, company_uuid)
    return None


# ---------------------------------------------------------------------------
# Pay lookup
# ---------------------------------------------------------------------------

def get_payrolls_for_month(
    access_token: str,
    company_uuid: str,
    year:         int,
    month:        int,
    api_base_url: str,
) -> list[dict]:
    """
    Return all payrolls (regular + off-cycle) whose pay period overlaps the
    given calendar month, with employee_compensations included.

    GET /v1/companies/{uuid}/payrolls
        ?start_date=YYYY-MM-01
        &end_date=YYYY-MM-{last}
        &include_off_cycle=true
        &include[]=employee_compensations
    """
    import calendar as _cal
    last_day   = _cal.monthrange(year, month)[1]
    start_date = f"{year}-{month:02d}-01"
    end_date   = f"{year}-{month:02d}-{last_day:02d}"

    # Build the query string manually — httpx encodes [] as %5B%5D which Gusto
    # does not recognise, causing the include param to be silently ignored.
    # Try both bracket and non-bracket forms so at least one is accepted.
    from urllib.parse import urlencode
    qs = urlencode([
        ("start_date",        start_date),
        ("end_date",          end_date),
        ("include_off_cycle", "true"),
        ("include",           "employee_compensations"),
    ])
    qs += "&include[]=employee_compensations"
    url = f"{api_base_url.rstrip('/')}/v1/companies/{company_uuid}/payrolls?{qs}"

    logger.info(
        "Gusto get_payrolls company=%s start=%s end=%s",
        company_uuid, start_date, end_date,
    )
    resp = httpx.get(url, headers=_auth_headers(access_token), timeout=30)
    if not resp.is_success:
        logger.error(
            "Gusto get_payrolls failed status=%d body=%s",
            resp.status_code, resp.text,
        )
    resp.raise_for_status()
    body = resp.json()
    # Gusto may return a dict with a "payrolls" key or a bare list
    payrolls = body if isinstance(body, list) else body.get("payrolls", [])
    for p in payrolls:
        comps = p.get("employee_compensations")
        logger.info(
            "Gusto payroll uuid=%s processed=%s off_cycle=%s "
            "compensations_key_present=%s compensations_count=%d",
            p.get("uuid"), p.get("processed"), p.get("off_cycle"),
            comps is not None, len(comps) if comps else 0,
        )
    logger.info(
        "Gusto get_payrolls company=%s start=%s end=%s returned=%d",
        company_uuid, start_date, end_date, len(payrolls),
    )
    return payrolls


# ---------------------------------------------------------------------------
# Off-cycle payroll
# ---------------------------------------------------------------------------

def create_off_cycle_payroll(
    access_token:  str,
    company_uuid:  str,
    employee_uuid: str,
    job_uuid:      str,
    amount:        float,
    currency:      str = "USD",
    api_base_url:  str = "https://api.gusto-demo.com",
) -> str:
    """
    Create, calculate, and submit an off-cycle bonus payroll for one employee.
    Returns the Gusto payroll UUID.

    Gusto off-cycle flow (v1 API):
      1. POST /v1/companies/{uuid}/payrolls           → creates draft
      2. POST /v1/companies/{uuid}/payrolls/{uuid}/calculate
      3. POST /v1/companies/{uuid}/payrolls/{uuid}/submit

    Raises httpx.HTTPStatusError on any Gusto API failure.
    """
    if currency != "USD":
        logger.warning(
            "Gusto only supports USD; submitting amount=%.2f as USD (requested %s)",
            amount, currency,
        )

    today = datetime.utcnow().date().isoformat()

    # 1. Create draft
    create_resp = httpx.post(
        f"{api_base_url.rstrip('/')}/v1/companies/{company_uuid}/payrolls",
        json={
            "off_cycle":        True,
            "off_cycle_reason": "Bonus",
            "start_date":       today,
            "end_date":         today,
            "employee_uuids":   [employee_uuid],
            "employee_compensations": [{
                "employee_uuid": employee_uuid,
                "fixed_compensations": [{
                    "name":     "Bonus",
                    "amount":   f"{amount:.2f}",
                    "job_uuid": job_uuid,
                }],
            }],
        },
        headers=_auth_headers(access_token),
        timeout=30,
    )
    if not create_resp.is_success:
        logger.error(
            "Gusto payroll creation failed status=%d body=%s",
            create_resp.status_code, create_resp.text,
        )
    create_resp.raise_for_status()
    payroll_uuid = create_resp.json()["uuid"]
    logger.info("Gusto payroll created uuid=%s employee=%s amount=%.2f",
                payroll_uuid, employee_uuid, amount)

    # 2. Calculate
    httpx.put(
        f"{api_base_url.rstrip('/')}/v1/companies/{company_uuid}/payrolls/{payroll_uuid}/calculate",
        headers=_auth_headers(access_token),
        timeout=30,
    ).raise_for_status()
    logger.info("Gusto payroll calculated uuid=%s", payroll_uuid)

    # 3. Submit
    httpx.put(
        f"{api_base_url.rstrip('/')}/v1/companies/{company_uuid}/payrolls/{payroll_uuid}/submit",
        headers=_auth_headers(access_token),
        timeout=30,
    ).raise_for_status()
    logger.info("Gusto payroll submitted uuid=%s", payroll_uuid)

    return payroll_uuid
