"""
gusto_client.py — Gusto Embedded API HTTP client
=================================================
Wraps all Gusto API calls used by the payroll broker:
  • OAuth token exchange / refresh
  • Company and employee discovery
  • Off-cycle bonus payroll creation, calculation, and submission

Base URLs come from environment variables so the same code runs against both
the Gusto sandbox (https://api.gusto-demo.com) and production.

Environment variables:
    GUSTO_API_BASE_URL    e.g. https://api.gusto-demo.com
    GUSTO_OAUTH_BASE_URL  e.g. https://api.gusto-demo.com  (may differ in prod)
    GUSTO_CLIENT_ID
    GUSTO_CLIENT_SECRET
    PAYROLL_BROKER_BASE_URL  e.g. https://payroll-broker.terianix.ai
                             (used to build the redirect_uri)
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_API_BASE   = os.getenv("GUSTO_API_BASE_URL",   "https://api.gusto-demo.com")
_OAUTH_BASE = os.getenv("GUSTO_OAUTH_BASE_URL",  "https://api.gusto-demo.com")
_CLIENT_ID  = os.getenv("GUSTO_CLIENT_ID",       "")
_CLIENT_SECRET = os.getenv("GUSTO_CLIENT_SECRET", "")
_BROKER_BASE   = os.getenv("PAYROLL_BROKER_BASE_URL", "https://payroll-broker.terianix.ai")

REDIRECT_URI = f"{_BROKER_BASE.rstrip('/')}/gusto/callback"
OAUTH_SCOPES = "openid employees:read:sensitive payrolls:run companies:read"


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def build_authorization_url(state: str) -> str:
    """
    Build the Gusto OAuth authorize URL to redirect the tenant admin to.

    The caller passes a URL-safe opaque state string (typically the internal
    tenant_id encoded as a string) so the callback can identify which tenant
    completed the OAuth flow.
    """
    from urllib.parse import urlencode
    params = {
        "response_type": "code",
        "client_id":     _CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "scope":         OAUTH_SCOPES,
        "state":         state,
    }
    return f"{_OAUTH_BASE}/oauth/authorize?{urlencode(params)}"


def exchange_code_for_token(code: str) -> dict:
    """
    Exchange an authorization code for access + refresh tokens.

    Returns a dict with keys:
        access_token, refresh_token, expires_at (datetime, UTC)

    Raises httpx.HTTPStatusError on non-2xx response.
    """
    resp = httpx.post(
        f"{_OAUTH_BASE}/oauth/token",
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
    payload = resp.json()

    expires_in  = int(payload.get("expires_in", 7200))
    expires_at  = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    return {
        "access_token":  payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at":    expires_at,
    }


def refresh_access_token(refresh_token: str) -> dict:
    """
    Use a refresh token to obtain a new access token.

    Returns the same dict shape as exchange_code_for_token().
    Raises httpx.HTTPStatusError on failure.
    """
    resp = httpx.post(
        f"{_OAUTH_BASE}/oauth/token",
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
    payload = resp.json()

    expires_in = int(payload.get("expires_in", 7200))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    return {
        "access_token":  payload["access_token"],
        "refresh_token": payload.get("refresh_token", refresh_token),
        "expires_at":    expires_at,
    }


# ---------------------------------------------------------------------------
# Company / employee discovery
# ---------------------------------------------------------------------------

def _auth_headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept":        "application/json",
        "Content-Type":  "application/json",
    }


def get_current_user(access_token: str) -> dict:
    """
    GET /v1/me — returns the authenticated user's profile including the
    list of companies they have access to.

    Used at OAuth callback time to extract the company UUID.
    """
    resp = httpx.get(
        f"{_API_BASE}/v1/me",
        headers=_auth_headers(access_token),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_company_uuid_from_me(access_token: str) -> Optional[str]:
    """
    Return the first company UUID from /v1/me, or None if none exist.
    Gusto's embedded model means each OAuth token is scoped to one company.
    """
    me = get_current_user(access_token)
    roles = me.get("roles", {})
    companies = []
    # Payroll Admin role gives company access
    payroll_admin = roles.get("payroll_admin", {})
    companies.extend(payroll_admin.get("companies", []))
    if companies:
        return companies[0].get("uuid")
    return None


def get_employees(access_token: str, company_uuid: str) -> list[dict]:
    """
    GET /v1/companies/{company_uuid}/employees
    Returns the full employee list for the company.

    Each item has: uuid, email, first_name, last_name, jobs (list with uuid).
    """
    resp = httpx.get(
        f"{_API_BASE}/v1/companies/{company_uuid}/employees",
        headers=_auth_headers(access_token),
        params={"include": "all_compensations"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def find_employee_by_email(
    access_token:  str,
    company_uuid:  str,
    email:         str,
) -> Optional[dict]:
    """
    Search the company's employee list for one whose work email matches.

    Returns a dict with keys: employee_uuid, job_uuid (first job)
    or None if not found.

    Gusto employees have a `work_email` field and optionally a personal
    `email`. We check both.
    """
    employees = get_employees(access_token, company_uuid)
    email_lower = email.lower()

    for emp in employees:
        emp_email      = (emp.get("work_email") or "").lower()
        emp_alt_email  = (emp.get("email")      or "").lower()
        if email_lower in (emp_email, emp_alt_email):
            jobs = emp.get("jobs", [])
            job_uuid = jobs[0]["uuid"] if jobs else None
            return {
                "employee_uuid": emp["uuid"],
                "job_uuid":      job_uuid,
            }

    logger.warning(
        "Gusto employee not found for email=%s company_uuid=%s",
        email, company_uuid,
    )
    return None


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
) -> str:
    """
    Create, populate, calculate, and submit an off-cycle bonus payroll for
    one employee.  Returns the Gusto payroll UUID.

    Gusto off-cycle payroll flow (v1 API):
      1. POST /v1/companies/{uuid}/payrolls           → creates draft, returns payroll_uuid
      2. PUT  /v1/companies/{uuid}/payrolls/{uuid}    → sets employee compensation
      3. POST /v1/companies/{uuid}/payrolls/{uuid}/calculate  → calculates net pay
      4. POST /v1/companies/{uuid}/payrolls/{uuid}/submit     → finalises

    The payroll is created with off_cycle_reason="Bonus".  Only USD is
    natively supported by Gusto; for other currencies we log a warning
    and submit the numeric amount as-is (useful in sandbox demos where
    currency is synthetic).

    Raises httpx.HTTPStatusError on any Gusto API failure.
    """
    if currency != "USD":
        logger.warning(
            "Gusto only supports USD; submitting amount=%.2f as USD (requested %s)",
            amount, currency,
        )

    # 1. Determine pay period dates (today ± 0 days for off-cycle bonus)
    today = datetime.utcnow().date().isoformat()

    # 2. Create draft off-cycle payroll
    create_resp = httpx.post(
        f"{_API_BASE}/v1/companies/{company_uuid}/payrolls",
        json={
            "off_cycle":        True,
            "off_cycle_reason": "Bonus",
            "start_date":       today,
            "end_date":         today,
            "employee_compensations": [
                {
                    "employee_uuid": employee_uuid,
                    "fixed_compensations": [
                        {
                            "name":     "Bonus",
                            "amount":   f"{amount:.2f}",
                            "job_uuid": job_uuid,
                        }
                    ],
                }
            ],
        },
        headers=_auth_headers(access_token),
        timeout=30,
    )
    create_resp.raise_for_status()
    payroll = create_resp.json()
    payroll_uuid = payroll["uuid"]

    logger.info(
        "Gusto off-cycle payroll created payroll_uuid=%s employee_uuid=%s amount=%.2f",
        payroll_uuid, employee_uuid, amount,
    )

    # 3. Calculate the payroll (POST per Gusto v1 API)
    calc_resp = httpx.post(
        f"{_API_BASE}/v1/companies/{company_uuid}/payrolls/{payroll_uuid}/calculate",
        headers=_auth_headers(access_token),
        timeout=30,
    )
    calc_resp.raise_for_status()
    logger.info("Gusto payroll calculated payroll_uuid=%s", payroll_uuid)

    # 4. Submit the payroll (POST per Gusto v1 API)
    submit_resp = httpx.post(
        f"{_API_BASE}/v1/companies/{company_uuid}/payrolls/{payroll_uuid}/submit",
        headers=_auth_headers(access_token),
        timeout=30,
    )
    submit_resp.raise_for_status()
    logger.info("Gusto payroll submitted payroll_uuid=%s", payroll_uuid)

    return payroll_uuid
