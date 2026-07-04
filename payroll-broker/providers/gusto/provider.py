"""
provider.py — GustoProvider
============================
Implements PayrollProvider for the Gusto Embedded API.

Responsibilities:
  • get_credentials  — returns a valid access token, refreshing via the
                       Gusto OAuth token endpoint if close to expiry.
  • find_employee    — looks up an employee by email within a Gusto company.
  • submit_payroll   — creates, calculates, and submits an off-cycle bonus
                       payroll via the Gusto v1 API.
  • validate_webhook — verifies X-Gusto-Signature (HMAC-SHA256, checks both
                       hex and base64 encodings).

All raw HTTP calls are delegated to client.py; no httpx usage lives here.
All DB writes (token upsert) are delegated to utils.sqlhelper.
"""

import base64
import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import utils.crypto as crypto
import utils.sqlhelper as db
from providers.base import PayrollProvider
from providers.gusto import client

logger = logging.getLogger(__name__)

# Refresh the access token if it expires within this window
_TOKEN_REFRESH_MARGIN = timedelta(minutes=10)

# Loaded once at process startup; set via GUSTO_WEBHOOK_SECRET env var
_WEBHOOK_SECRET: str = os.getenv("GUSTO_WEBHOOK_SECRET", "")


class GustoProvider(PayrollProvider):
    """Gusto Embedded API payroll provider."""

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "gusto"

    # ── Credentials ───────────────────────────────────────────────────────────

    def get_credentials(self, provider_row, token_row) -> dict:
        """
        Return {"access_token": str} for immediate Gusto API use.

        Refreshes the token if it is within 10 minutes of expiry and
        persists the new token + refresh_token to dbo.payroll_tokens.

        Raises RuntimeError if no token exists (OAuth flow not completed).
        """
        if not token_row:
            raise RuntimeError(
                f"No Gusto OAuth token for provider_id={provider_row.id}. "
                "The tenant must complete the Gusto OAuth flow first "
                "(/gusto/authorize?tenant_id=<id>)."
            )

        oauth_base_url = provider_row.oauth_base_url or "https://api.gusto-demo.com"
        api_base_url   = provider_row.api_base_url   or "https://api.gusto-demo.com"

        # Decrypt stored ciphertext — plaintext only lives in memory from here
        access_token  = crypto.decrypt(token_row.access_token)
        refresh_token = crypto.decrypt(token_row.refresh_token)

        if self._token_needs_refresh(token_row):
            logger.info(
                "Refreshing Gusto access token provider_id=%d", provider_row.id
            )
            try:
                refreshed = client.refresh_access_token(
                    refresh_token,
                    oauth_base_url=oauth_base_url,
                )
            except Exception:
                logger.exception(
                    "Gusto token refresh failed provider_id=%d", provider_row.id
                )
                raise

            db.upsert_payroll_token(
                provider_id=provider_row.id,
                access_token=crypto.encrypt(refreshed["access_token"]),
                refresh_token=crypto.encrypt(refreshed["refresh_token"]),
                token_expires_at=refreshed["expires_at"],
            )
            return {
                "access_token": refreshed["access_token"],
                "api_base_url": api_base_url,
            }

        return {
            "access_token": access_token,
            "api_base_url": api_base_url,
        }

    def _token_needs_refresh(self, token_row) -> bool:
        if not token_row.token_expires_at:
            return False
        expiry = token_row.token_expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= (expiry - _TOKEN_REFRESH_MARGIN)

    # ── Payroll operations ────────────────────────────────────────────────────

    def find_employee(
        self,
        credentials: dict,
        company_ref: str,
        email:       str,
    ) -> dict:
        """
        Find an employee in Gusto by email and return {employee_id, job_id}.

        Raises RuntimeError if not found or if the employee has no jobs.
        """
        access_token = credentials["access_token"]
        api_base_url = credentials.get("api_base_url", "https://api.gusto-demo.com")
        result = client.find_employee_by_email(access_token, company_ref, email, api_base_url)

        if not result:
            raise RuntimeError(
                f"Beneficiary email={email} not found in "
                f"Gusto company={company_ref}"
            )

        if not result.get("job_id"):
            raise RuntimeError(
                f"No job found for Gusto employee_id={result['employee_id']} "
                "— cannot create payroll without a job UUID"
            )

        return result

    def submit_payroll(
        self,
        credentials:  dict,
        company_ref:  str,
        employee_id:  str,
        job_id:       Optional[str],
        amount:       float,
        currency:     str,
    ) -> str:
        """
        Create, calculate, and submit a Gusto off-cycle bonus payroll.
        Returns the Gusto payroll UUID.
        """
        return client.create_off_cycle_payroll(
            access_token=credentials["access_token"],
            company_uuid=company_ref,
            employee_uuid=employee_id,
            job_uuid=job_id,
            amount=amount,
            currency=currency,
            api_base_url=credentials.get("api_base_url", "https://api.gusto-demo.com"),
        )

    def get_employee_pay(
        self,
        credentials: dict,
        company_ref: str,
        upn:         str,
        year:        int,
        month:       int,
    ) -> list[dict]:
        """
        Fetch all payroll entries (regular + off-cycle) for an employee for
        a given calendar month.

        Steps:
          1. Find employee_uuid by matching upn against work_email / email.
          2. Fetch payrolls for the month with employee_compensations.
          3. Filter compensation rows to this employee_uuid.
          4. Return structured list.
        """
        access_token = credentials["access_token"]
        api_base_url = credentials.get("api_base_url", "https://api.gusto-demo.com")

        # 1. Resolve Gusto employee UUID from UPN
        logger.info(
            "get_employee_pay resolving employee upn=%s company=%s", upn, company_ref
        )
        emp = client.find_employee_by_email(access_token, company_ref, upn, api_base_url)
        if not emp:
            logger.warning(
                "get_employee_pay employee not found upn=%s company=%s", upn, company_ref
            )
            raise RuntimeError(
                f"Employee upn={upn} not found in Gusto company={company_ref}"
            )
        employee_uuid = emp["employee_id"]
        profile = {
            "employee_uuid": employee_uuid,
            "full_name":     emp.get("full_name", ""),
            "work_email":    emp.get("work_email", ""),
            "address":       emp.get("address", {}),
            "payrate":       emp.get("payrate", {}),
        }
        logger.info(
            "get_employee_pay employee resolved upn=%s employee_uuid=%s",
            upn, employee_uuid,
        )

        # 2. Fetch payrolls for the month
        payrolls = client.get_payrolls_for_month(
            access_token, company_ref, year, month, api_base_url
        )
        logger.info(
            "get_employee_pay payrolls_in_month=%d upn=%s year=%d month=%d",
            len(payrolls), upn, year, month,
        )

        # 3. Filter and shape
        entries = []
        for payroll in payrolls:
            p_uuid   = payroll.get("uuid", "")
            p_type   = "off_cycle" if payroll.get("off_cycle") else "regular"
            comps    = payroll.get("employee_compensations", [])
            emp_comp = next(
                (c for c in comps if c.get("employee_uuid") == employee_uuid),
                None,
            )
            if not emp_comp:
                logger.debug(
                    "get_employee_pay no compensation match payroll_uuid=%s type=%s "
                    "employee_uuid=%s compensations_in_payroll=%d",
                    p_uuid, p_type, employee_uuid, len(comps),
                )
                continue

            pay_period = payroll.get("pay_period", {})
            gross      = float(emp_comp.get("gross_pay")  or 0)
            net        = float(emp_comp.get("net_pay")    or 0)
            logger.info(
                "get_employee_pay match payroll_uuid=%s type=%s period=%s→%s "
                "gross=%.2f net=%.2f",
                p_uuid, p_type,
                pay_period.get("start_date", ""),
                pay_period.get("end_date",   ""),
                gross, net,
            )

            entries.append({
                "payroll_uuid":     p_uuid,
                "payroll_type":     p_type,
                "pay_period_start": pay_period.get("start_date", ""),
                "pay_period_end":   pay_period.get("end_date",   ""),
                "check_date":       payroll.get("check_date"),
                "gross_pay":        gross,
                "net_pay":          net,
                "total_deductions": round(gross - net, 2),
            })

        logger.info(
            "get_employee_pay upn=%s year=%d month=%d entries=%d",
            upn, year, month, len(entries),
        )
        return {"profile": profile, "entries": entries}

    # ── Webhook validation ────────────────────────────────────────────────────

    def validate_webhook(self, body: bytes, headers: dict) -> bool:
        """
        Verify Gusto's X-Gusto-Signature header (HMAC-SHA256).

        Gusto signs the raw body with GUSTO_WEBHOOK_SECRET.  The sandbox
        sends hex-encoded digests; production may send base64.  We check
        both in constant time to handle either.
        """
        if not _WEBHOOK_SECRET:
            logger.error("GUSTO_WEBHOOK_SECRET not set — cannot validate webhook")
            return False

        # Headers dict may use any casing depending on the ASGI framework
        sig = (
            headers.get("X-Gusto-Signature")
            or headers.get("x-gusto-signature")
            or ""
        )
        if not sig:
            return False

        digest = hmac.new(
            _WEBHOOK_SECRET.encode("utf-8"),
            msg=body,
            digestmod=hashlib.sha256,
        ).digest()

        hex_sig    = digest.hex()
        base64_sig = base64.b64encode(digest).decode("utf-8")

        return (
            hmac.compare_digest(hex_sig,    sig) or
            hmac.compare_digest(base64_sig, sig)
        )
