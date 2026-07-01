"""
oauth_router.py — Gusto OAuth 2.0 onboarding endpoints
=======================================================
  GET /gusto/authorize?tenant_id=<id>
      Redirects the tenant admin to Gusto's OAuth consent page.

  GET /gusto/callback?code=<code>&state=<tenant_id>
      Exchanges the code for tokens, writes company_id_at_provider to
      payroll_providers, and upserts payroll_tokens.

Pre-requisite: an admin must have created a payroll_providers row with
name="gusto" and linked it to the tenant via Tenants.payroll_provider_id
before this flow can run.

Security note: state is the raw tenant_id in sandbox.  Production should
use a signed, time-limited CSRF token.
"""

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

import utils.sqlhelper as db
from providers.gusto import client
from routers.schemas import GustoCallbackResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gusto", tags=["gusto-oauth"])


@router.get("/authorize")
def gusto_authorize(tenant_id: int = Query(..., description="Internal tenant ID")):
    """
    Redirect tenant admin to Gusto OAuth consent page.

    Redirect URI registered in the Gusto developer portal:
        https://payroll-broker.terianix.ai/gusto/callback
    """
    auth_url = client.build_authorization_url(state=str(tenant_id))
    logger.info("Redirecting tenant_id=%d to Gusto OAuth", tenant_id)
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/callback", response_model=GustoCallbackResponse)
def gusto_callback(
    code:  str = Query(..., description="Authorization code from Gusto"),
    state: str = Query(..., description="tenant_id passed as OAuth state"),
):
    """
    Gusto OAuth callback — exchange code for tokens, persist in DB.

      1. Parse tenant_id from state
      2. Verify the tenant has a configured payroll_providers row
      3. Exchange the authorization code for access + refresh tokens
      4. Fetch the Gusto company UUID via GET /v1/me
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
        logger.error(
            "No payroll provider configured for tenant_id=%d", tenant_id
        )
        raise HTTPException(
            status_code=422,
            detail=(
                "No payroll provider is configured for this tenant. "
                "An administrator must create a payroll_providers row and link "
                "it to the tenant before running the OAuth flow."
            ),
        )

    if provider_row.name != "gusto":
        raise HTTPException(
            status_code=422,
            detail=(
                f"Tenant's provider is '{provider_row.name}', not 'gusto'. "
                "Use the correct OAuth endpoint for that provider."
            ),
        )

    # 3. Exchange code for tokens
    try:
        token_data = client.exchange_code_for_token(code)
    except Exception as exc:
        logger.exception("Gusto token exchange failed tenant_id=%d", tenant_id)
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}")

    access_token  = token_data["access_token"]
    refresh_token = token_data["refresh_token"]
    expires_at    = token_data["expires_at"]

    # 4. Fetch Gusto company UUID
    try:
        company_id = client.get_company_uuid_from_me(access_token)
    except Exception as exc:
        logger.exception("Gusto /v1/me failed tenant_id=%d", tenant_id)
        raise HTTPException(status_code=502, detail=f"Failed to fetch Gusto company: {exc}")

    if not company_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "No Gusto company found for this account. "
                "Ensure the account has Payroll Admin access to at least one company."
            ),
        )

    # 5. Persist company reference
    db.update_provider_company_ref(provider_row.id, company_id)

    # 6. Upsert tokens
    db.upsert_payroll_token(
        provider_id=provider_row.id,
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=expires_at,
    )

    logger.info(
        "Gusto OAuth completed tenant_id=%d provider_id=%d company_id_at_provider=%s",
        tenant_id, provider_row.id, company_id,
    )

    return GustoCallbackResponse(
        message="Gusto integration connected successfully.",
        tenant_id=tenant_id,
        provider_id=provider_row.id,
        company_id_at_provider=company_id,
    )
