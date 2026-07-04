"""
base.py — PayrollProvider abstract base class
==============================================
All payroll provider implementations must subclass PayrollProvider and
implement its abstract methods.  The ABC enforces the contract at
instantiation time: a missing method raises TypeError immediately rather
than at the point the worker tries to call it.

Adding a new provider
---------------------
1. Create providers/<name>/ package with:
     client.py       — thin HTTP wrapper (no business logic)
     provider.py     — class <Name>Provider(PayrollProvider)
     oauth_router.py — /name/authorize + /name/callback  (if OAuth)
     webhook_router.py — /name/webhook  (if push callbacks)
2. Register it in providers/registry.py
3. Include its routers in main.py

Credential model
----------------
Credentials are passed as a plain dict so the contract is not tied to any
one auth scheme:
    OAuth2 providers (Gusto):
        {"access_token": "..."}
    Service-account providers (Workday):
        {"username": "...", "password": "..."}
    API-key providers:
        {"api_key": "..."}

The worker calls get_credentials() and passes the result opaquely to
find_employee() and submit_payroll().  Only the provider implementation
knows what the dict contains.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class PayrollProvider(ABC):
    """
    Abstract base class for payroll provider integrations.

    Sub-classes are registered in providers/registry.py and instantiated
    once at process startup.  All methods must be thread-safe (they may be
    called concurrently if the worker is ever parallelised).
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Lowercase provider identifier — must match payroll_providers.name in
        the DB.  Examples: "gusto", "workday", "adp".
        """
        ...

    # ── Credentials ───────────────────────────────────────────────────────────

    @abstractmethod
    def get_credentials(self, provider_row, token_row) -> dict:
        """
        Return a credentials dict valid for immediate API use.

        Args:
            provider_row: PayrollProviderORM row for this provider instance.
            token_row:    PayrollTokenORM row, or None if OAuth not completed.

        For OAuth2 providers this method is responsible for refreshing the
        access token when it is close to expiry and persisting the new token
        to the DB before returning.

        Raises RuntimeError if credentials are not available (e.g. OAuth flow
        not completed, service-account config missing).
        """
        ...

    # ── Payroll operations ────────────────────────────────────────────────────

    @abstractmethod
    def find_employee(
        self,
        credentials:  dict,
        company_ref:  str,
        email:        str,
    ) -> dict:
        """
        Locate an employee by email address within the provider's company.

        Args:
            credentials: Dict returned by get_credentials().
            company_ref: Provider's company identifier
                         (payroll_providers.company_id_at_provider).
            email:       The beneficiary's work or personal email.

        Returns:
            A dict with at least {"employee_id": str, "job_id": str | None}.
            Keys are provider-specific; the worker passes the dict straight
            back to submit_payroll().

        Raises RuntimeError if the employee is not found.
        """
        ...

    @abstractmethod
    def submit_payroll(
        self,
        credentials:   dict,
        company_ref:   str,
        employee_id:   str,
        job_id:        Optional[str],
        amount:        float,
        currency:      str,
    ) -> str:
        """
        Create, calculate, and submit an off-cycle bonus payroll run.

        Returns the provider's external reference for this payroll
        (e.g. Gusto payroll UUID, Workday transaction ID).  This reference
        is stored in payroll_submissions.provider_payroll_ref and used later
        to match webhook callbacks back to the nomination.

        Raises httpx.HTTPStatusError (or provider-equivalent) on API failure.
        """
        ...

    @abstractmethod
    def get_employee_pay(
        self,
        credentials: dict,
        company_ref: str,
        upn:         str,
        year:        int,
        month:       int,
    ) -> dict:
        """
        Return employee profile + payroll entries for a given calendar month.

        Args:
            credentials: Dict returned by get_credentials().
            company_ref: Provider's company identifier.
            upn:         Employee's userPrincipalName (work email in most providers).
            year:        Calendar year  (e.g. 2026).
            month:       Calendar month (1–12).

        Returns:
            {
              "profile": {
                "employee_uuid": str,
                "full_name":     str,
                "work_email":    str,
                "address":       {"street_1", "street_2", "city", "state", "zip"},
                "payrate":       {"rate": str, "payment_unit": str},
              },
              "entries": [
                {
                  payroll_uuid, payroll_type, pay_period_start, pay_period_end,
                  check_date, gross_pay, net_pay, total_deductions
                },
                ...
              ]
            }

        Raises RuntimeError if the employee is not found.
        """
        ...

    # ── Webhook validation ────────────────────────────────────────────────────

    def validate_webhook(self, body: bytes, headers: dict) -> bool:
        """
        Verify the authenticity of an inbound webhook callback.

        Default implementation returns True — suitable for providers that
        use network-level security (IP allowlist, mTLS) rather than a
        payload signature.  Override for providers that embed an HMAC or
        token in the request headers (e.g. Gusto's X-Gusto-Signature).

        Args:
            body:    Raw request body bytes (before JSON parsing).
            headers: Request headers as a plain dict (lower-cased keys are
                     fine — implementations should check both cases or
                     normalise before comparing).

        Returns True if the request is authentic, False otherwise.
        """
        return True
