"""
provider.py — RipplingProvider
================================
Implements PayrollProvider for the Rippling Platform API.

Responsibilities:
  • get_credentials  — returns a valid access token, refreshing if close to expiry.
  • find_employee    — looks up an employee by email (stub: returns seeded data).
  • submit_payroll   — creates an off-cycle bonus payroll run via Rippling API
                       (stub: returns a fake run ID).
  • get_employee_pay — returns profile + payroll entries for a given month.
                       List endpoint returns [] in stub mode; DB-backed
                       extra_payroll_refs are fetched individually via
                       get_payroll_run_by_id (same hybrid pattern as Gusto).
  • validate_webhook — verifies X-Rippling-Signature HMAC-SHA256.

All raw HTTP calls are delegated to client.py.
All DB writes (token upsert) are delegated to utils.sqlhelper.
"""

import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import utils.crypto as crypto
import utils.sqlhelper as db
from providers.base import PayrollProvider
from providers.rippling import client

logger = logging.getLogger(__name__)

_TOKEN_REFRESH_MARGIN  = timedelta(minutes=10)
_WEBHOOK_SECRET: str   = os.getenv("RIPPLING_WEBHOOK_SECRET", "")


class RipplingProvider(PayrollProvider):
    """Rippling Platform API payroll provider."""

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "rippling"

    # ── Credentials ───────────────────────────────────────────────────────────

    def get_credentials(self, provider_row, token_row) -> dict:
        """
        Return {"access_token": str, "api_base_url": str, "company_id": str}
        for immediate Rippling API use.

        In stub mode the token check is skipped — stub client functions do not
        make real HTTP calls and do not require valid credentials.
        """
        api_base_url   = provider_row.api_base_url   or "https://rest.ripplingsandboxapis.com"
        oauth_base_url = provider_row.oauth_base_url or "https://rest.ripplingsandboxapis.com"
        company_id     = provider_row.company_id_at_provider or "stub-rippling-co-001"

        stub_mode = os.getenv("RIPPLING_STUB_MODE", "true").lower() == "true"

        if stub_mode:
            logger.info(
                "RipplingProvider.get_credentials STUB MODE — skipping token check "
                "provider_id=%d", provider_row.id
            )
            return {
                "access_token": "stub_rippling_access_token",
                "api_base_url": api_base_url,
                "company_id":   company_id,
            }

        if not token_row:
            raise RuntimeError(
                f"No Rippling OAuth token for provider_id={provider_row.id}. "
                "The tenant must complete the Rippling OAuth flow first "
                "(/rippling/authorize?tenant_id=<id>)."
            )

        access_token  = crypto.decrypt(token_row.access_token)
        refresh_token = crypto.decrypt(token_row.refresh_token)

        if self._token_needs_refresh(token_row):
            logger.info(
                "Refreshing Rippling access token provider_id=%d", provider_row.id
            )
            try:
                refreshed = client.refresh_access_token(
                    refresh_token,
                    oauth_base_url=oauth_base_url,
                )
            except Exception:
                logger.exception(
                    "Rippling token refresh failed provider_id=%d", provider_row.id
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
                "company_id":   company_id,
            }

        return {
            "access_token": access_token,
            "api_base_url": api_base_url,
            "company_id":   company_id,
        }

    def _token_needs_refresh(self, token_row) -> bool:
        if not token_row or not token_row.token_expires_at:
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
        Find an employee in Rippling by email and return {employee_id, job_id, ...}.

        Raises RuntimeError if not found.
        Note: Rippling does not use job UUIDs for payroll submissions (job_id=None).
        """
        result = client.find_employee_by_email(
            access_token=credentials["access_token"],
            email=email,
            api_base_url=credentials.get("api_base_url", "https://rest.ripplingsandboxapis.com"),
        )
        if not result:
            raise RuntimeError(
                f"Beneficiary email={email} not found in Rippling company={company_ref}"
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
        Create and submit a Rippling off-cycle award bonus payroll run.
        Returns the Rippling payroll run ID (stored as provider_payroll_ref).

        Note: Rippling does not use job UUIDs — job_id is accepted but ignored.
        """
        return client.create_off_cycle_payroll_run(
            access_token=credentials["access_token"],
            company_id=credentials.get("company_id", company_ref),
            employee_id=employee_id,
            amount=amount,
            currency=currency,
            api_base_url=credentials.get("api_base_url", "https://rest.ripplingsandboxapis.com"),
        )

    def get_employee_pay(
        self,
        credentials:        dict,
        company_ref:        str,
        upn:                str,
        year:               int,
        month:              int,
        extra_payroll_refs: list[str] | None = None,
    ) -> dict:
        """
        Return employee profile + payroll entries for a given calendar month.

        Steps:
          1. Resolve employee profile by UPN via find_employee_by_email.
          2. Fetch payroll runs for the month (returns [] in stub mode).
          3. Supplement with DB-known run IDs (extra_payroll_refs) not already
             in the list result — fetches each by ID and appends to entries.
             This is the same hybrid DB-backed pattern used for Gusto sandbox.
        """
        access_token = credentials["access_token"]
        api_base_url = credentials.get("api_base_url", "https://rest.ripplingsandboxapis.com")
        company_id   = credentials.get("company_id", company_ref)

        # 1. Resolve employee profile
        logger.info(
            "RipplingProvider.get_employee_pay resolving employee upn=%s", upn
        )
        emp = client.find_employee_by_email(access_token, upn, api_base_url)
        if not emp:
            raise RuntimeError(
                f"Employee upn={upn} not found in Rippling company={company_ref}"
            )

        employee_id = emp["employee_id"]
        profile = {
            "employee_uuid": employee_id,
            "full_name":     emp.get("full_name", ""),
            "work_email":    emp.get("work_email", ""),
            "address":       emp.get("address", {}),
            "payrate":       emp.get("payrate", {}),
        }
        logger.info(
            "RipplingProvider.get_employee_pay employee resolved upn=%s id=%s",
            upn, employee_id,
        )

        # 2. Fetch payroll runs for the month from the list endpoint
        runs = client.get_payroll_runs_for_month(
            access_token, company_id, year, month, api_base_url
        )
        logger.info(
            "RipplingProvider.get_employee_pay list_runs=%d upn=%s year=%d month=%d",
            len(runs), upn, year, month,
        )

        entries = []
        for run in runs:
            run_id    = run.get("id", "")
            run_type  = run.get("payrollRunType", "")
            pay_items = run.get("payItems", [])

            # Find the pay item for this employee
            pay_item = next(
                (p for p in pay_items if p.get("employeeId") == employee_id),
                None,
            )
            if not pay_item:
                continue

            gross     = float(pay_item.get("grossPay") or 0)
            net       = float(pay_item.get("netPay")   or 0)
            earnings  = pay_item.get("earnings", [])
            comp_type = earnings[0].get("earningType") if earnings else None

            entries.append({
                "payroll_uuid":     run_id,
                "payroll_type":     "off_cycle" if run_type == "OFF_CYCLE" else "regular",
                "pay_period_start": run.get("payPeriodStartDate", ""),
                "pay_period_end":   run.get("payPeriodEndDate",   ""),
                "check_date":       run.get("checkDate"),
                "gross_pay":        gross,
                "net_pay":          net,
                "total_deductions": round(gross - net, 2),
                "comp_type":        comp_type,
            })

        # 3. Supplement with DB-known run IDs not in the list result
        # In stub mode the list always returns [], so all submitted nominations
        # are resolved here via the payroll_submissions.provider_payroll_ref.
        if extra_payroll_refs:
            fetched_ids = {e["payroll_uuid"] for e in entries}
            for ref in extra_payroll_refs:
                if ref in fetched_ids:
                    logger.info(
                        "RipplingProvider extra_ref already in list, skipping ref=%s", ref
                    )
                    continue
                logger.info("RipplingProvider fetching extra_ref=%s", ref)
                run = client.get_payroll_run_by_id(access_token, ref, api_base_url)
                if not run:
                    logger.warning(
                        "RipplingProvider extra_ref not retrievable ref=%s", ref
                    )
                    continue

                # In the real API each payItem has an employeeId.
                # In stub mode there is only one payItem and no employeeId —
                # we accept it unconditionally (the ref is already scoped to
                # this employee via payroll_submissions).
                pay_items = run.get("payItems", [])
                pay_item  = next(
                    (p for p in pay_items
                     if not p.get("employeeId") or p.get("employeeId") == employee_id),
                    pay_items[0] if pay_items else None,
                )
                if not pay_item:
                    logger.info(
                        "RipplingProvider extra_ref no pay item for employee "
                        "ref=%s employee_id=%s", ref, employee_id,
                    )
                    continue

                gross     = float(pay_item.get("grossPay") or 0)
                net       = float(pay_item.get("netPay")   or 0)
                earnings  = pay_item.get("earnings", [])
                comp_type = earnings[0].get("earningType") if earnings else None

                logger.info(
                    "RipplingProvider extra_ref matched ref=%s gross=%.2f net=%.2f comp_type=%s",
                    ref, gross, net, comp_type,
                )
                entries.append({
                    "payroll_uuid":     ref,
                    "payroll_type":     "off_cycle",
                    "pay_period_start": run.get("payPeriodStartDate", ""),
                    "pay_period_end":   run.get("payPeriodEndDate",   ""),
                    "check_date":       run.get("checkDate"),
                    "gross_pay":        gross,
                    "net_pay":          net,
                    "total_deductions": round(gross - net, 2),
                    "comp_type":        comp_type,
                })

        logger.info(
            "RipplingProvider.get_employee_pay upn=%s year=%d month=%d entries=%d",
            upn, year, month, len(entries),
        )
        return {"profile": profile, "entries": entries}

    # ── Webhook validation ────────────────────────────────────────────────────

    def validate_webhook(self, body: bytes, headers: dict) -> bool:
        """
        Verify Rippling's X-Rippling-Signature header (HMAC-SHA256).

        In stub mode: always returns True (no real webhooks arrive).
        """
        stub_mode = os.getenv("RIPPLING_STUB_MODE", "true").lower() == "true"
        if stub_mode:
            return True

        if not _WEBHOOK_SECRET:
            logger.error("RIPPLING_WEBHOOK_SECRET not set — cannot validate webhook")
            return False

        sig = (
            headers.get("X-Rippling-Signature")
            or headers.get("x-rippling-signature")
            or ""
        )
        if not sig:
            return False

        expected = hmac.new(
            _WEBHOOK_SECRET.encode("utf-8"),
            msg=body,
            digestmod=hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, sig)
