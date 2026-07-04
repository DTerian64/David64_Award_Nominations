"""
routers/employee_pay_router.py
==============================
Internal endpoint: look up an employee's pay for a given month.

Called exclusively by the backend's payroll_router — not exposed to the
public internet.  Auth is handled by the backend (AAD + PayrollBP role);
this router trusts the caller is already authorised.

Route
-----
GET /employee-pay?tenant_id=T&upn=X&year=YYYY&month=MM

Dispatches to the tenant's configured provider via PROVIDER_REGISTRY so
adding a new provider (Workday, ADP, …) requires zero changes here.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status

import utils.sqlhelper as db
from providers.registry import PROVIDER_REGISTRY

logger = logging.getLogger(__name__)

router = APIRouter(tags=["employee-pay"])


@router.get("/employee-pay")
def get_employee_pay(
    tenant_id: int = Query(...),
    upn:       str = Query(...),
    year:      int = Query(..., ge=2000, le=2100),
    month:     int = Query(..., ge=1,    le=12),
):
    """
    Return cycled and off-cycle payroll entries for one employee / one month.

    Dispatches to the correct PayrollProvider via PROVIDER_REGISTRY using
    the provider name stored in dbo.payroll_providers for this tenant.
    """
    # 1. Resolve provider row for the tenant
    provider_row = db.get_provider_for_tenant(tenant_id)
    if not provider_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No payroll provider configured for tenant_id={tenant_id}",
        )

    # 2. Look up provider implementation
    provider = PROVIDER_REGISTRY.get(provider_row.name)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Provider '{provider_row.name}' is not implemented",
        )

    # 3. Load credentials (handles token refresh internally)
    token_row = db.get_payroll_token_by_provider_id(provider_row.id)
    try:
        credentials = provider.get_credentials(provider_row, token_row)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    company_ref = provider_row.company_id_at_provider
    if not company_ref:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Provider OAuth not completed — company reference missing",
        )

    # 4. Fetch pay data
    try:
        result = provider.get_employee_pay(
            credentials=credentials,
            company_ref=company_ref,
            upn=upn,
            year=year,
            month=month,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.exception(
            "get_employee_pay failed tenant=%d upn=%s year=%d month=%d",
            tenant_id, upn, year, month,
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return {"profile": result.get("profile"), "entries": result.get("entries", [])}
