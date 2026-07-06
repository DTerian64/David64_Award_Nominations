"""
oauth_router.py — Rippling OAuth 2.0 onboarding endpoints
===========================================================
  GET /rippling/authorize?tenant_id=<id>
      Redirects the tenant admin to Rippling's OAuth consent page.

  GET /rippling/callback?code=<code>&state=<tenant_id>
      Exchanges the code for tokens, writes company_id_at_provider to
      payroll_providers, and upserts payroll_tokens.

In STUB MODE (RIPPLING_STUB_MODE=true) the callback accepts any code and
writes stub credentials to the DB so the rest of the integration works
without real Rippling credentials.

Pre-requisite: an admin must have created a payroll_providers row with
name="rippling" and linked it to the tenant via Tenants.payroll_provider_id.

Security note: state is the raw tenant_id in sandbox. Production should
use a signed, time-limited CSRF token.
"""

import logging
import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

import utils.crypto as crypto
import utils.sqlhelper as db
from providers.rippling import client
from routers.schemas import RipplingCallbackResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rippling", tags=["rippling-oauth"])

_STUB_MODE = os.getenv("RIPPLING_STUB_MODE", "true").lower() == "true"


@router.get("/authorize")
def rippling_authorize(tenant_id: int = Query(..., description="Internal tenant ID")):
    """
    Redirect tenant admin to Rippling OAuth consent page.

    In stub mode: redirects directly to the callback with a fake code so the
    full OAuth flow can be exercised without a real Rippling account.

    Redirect URI registered in the Rippling developer portal:
        https://payroll-broker.terianix.ai/rippling/callback
    """
    provider_row = db.get_provider_for_tenant(tenant_id)
    if not provider_row:
        raise HTTPException(
            status_code=422,
            detail="No payroll provider configured for this tenant.",
        )
    if provider_row.name != "rippling":
        raise HTTPException(
            status_code=422,
            detail=(
                f"Tenant's provider is '{provider_row.name}', not 'rippling'. "
                "Use the correct OAuth endpoint for that provider."
            ),
        )

    oauth_base_url = provider_row.oauth_base_url or "https://rest.ripplingsandboxapis.com"

    if _STUB_MODE:
        logger.info(
            "Rippling STUB MODE — skipping real OAuth, redirecting to stub callback "
            "tenant_id=%d", tenant_id
        )
        broker_base = os.getenv("PAYROLL_BROKER_BASE_URL", "https://payroll-broker.terianix.ai")
        stub_url    = f"{broker_base.rstrip('/')}/rippling/callback?code=stub_code&state={tenant_id}"
        return RedirectResponse(url=stub_url, status_code=302)

    auth_url = client.build_authorization_url(
        state=str(tenant_id),
        oauth_base_url=oauth_base_url,
    )
    logger.info("Redirecting tenant_id=%d to Rippling OAuth", tenant_id)
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/callback", response_model=RipplingCallbackResponse)
def rippling_callback(
    code:  str = Query(..., description="Authorization code from Rippling"),
    state: str = Query(..., description="tenant_id passed as OAuth state"),
):
    """
    Rippling OAuth callback — exchange code for tokens, persist in DB.

      1. Parse tenant_id from state
      2. Verify the tenant has a configured rippling payroll_providers row
      3. Exchange the authorization code for access + refresh tokens
      4. Fetch the Rippling company ID
      5. Write company_id_at_provider → payroll_providers
      6. Upsert payroll_tokens keyed by provider_id
    """
    # 1. Resolve tenant
    try:
        tenant_id = int(state)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    # 2. Look up configured provider
    provider_row = db.get_provider_for_tenant(tenant_id)
    if not provider_row:
        raise HTTPException(
            status_code=422,
            detail=(
                "No payroll provider configured for this tenant. "
                "An administrator must create a payroll_providers row and link "
                "it to the tenant before running the OAuth flow."
            ),
        )
    if provider_row.name != "rippling":
        raise HTTPException(
            status_code=422,
            detail=(
                f"Tenant's provider is '{provider_row.name}', not 'rippling'. "
                "Use the correct OAuth endpoint for that provider."
            ),
        )

    oauth_base_url = provider_row.oauth_base_url or "https://rest.ripplingsandboxapis.com"
    api_base_url   = provider_row.api_base_url   or "https://rest.ripplingsandboxapis.com"

    # 3. Exchange code for tokens (stub returns synthetic tokens)
    try:
        token_data = client.exchange_code_for_token(code, oauth_base_url=oauth_base_url)
    except Exception as exc:
        logger.exception("Rippling token exchange failed tenant_id=%d", tenant_id)
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}")

    access_token  = token_data["access_token"]
    refresh_token = token_data["refresh_token"]
    expires_at    = token_data["expires_at"]

    # 4. Fetch company ID (stub returns a fixed ID)
    try:
        company_id = client.get_current_company(access_token, api_base_url=api_base_url)
    except Exception as exc:
        logger.exception("Rippling company lookup failed tenant_id=%d", tenant_id)
        raise HTTPException(status_code=502, detail=f"Failed to fetch Rippling company: {exc}")

    if not company_id:
        raise HTTPException(
            status_code=422,
            detail="No Rippling company found for this account.",
        )

    # 5. Persist company reference
    db.update_provider_company_ref(provider_row.id, company_id)

    # 6. Upsert tokens (encrypted)
    db.upsert_payroll_token(
        provider_id=provider_row.id,
        access_token=crypto.encrypt(access_token),
        refresh_token=crypto.encrypt(refresh_token),
        token_expires_at=expires_at,
    )

    logger.info(
        "Rippling OAuth completed tenant_id=%d provider_id=%d company_id=%s stub=%s",
        tenant_id, provider_row.id, company_id, _STUB_MODE,
    )

    return RipplingCallbackResponse(
        message="Rippling integration connected successfully."
        + (" (STUB MODE)" if _STUB_MODE else ""),
        tenant_id=tenant_id,
        provider_id=provider_row.id,
        company_id_at_provider=company_id,
    )
