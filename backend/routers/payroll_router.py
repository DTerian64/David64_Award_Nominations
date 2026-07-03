"""
routers/payroll_router.py
=========================
PayrollBP-gated endpoints that proxy pay-data queries to the payroll-broker.

Routes
------
GET /api/payroll/employee-pay  — look up cycled + off-cycle pay for an employee
                                 for a given month (PayrollBP role required)

The backend owns auth (AAD token) and tenant resolution; the payroll-broker
owns provider routing (Gusto, Workday, …) and credential management.
The broker URL is configured via PAYROLL_BROKER_INTERNAL_URL env var.
"""

import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status

import utils.sqlhelper2 as sqlhelper
from auth import get_current_user_with_impersonation
from routers.schemas import EmployeePayResponse, PayrollEntry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["payroll"])

_BROKER_URL = os.getenv("PAYROLL_BROKER_INTERNAL_URL", "").rstrip("/")


def _require_payroll_bp(user_context: dict) -> dict:
    """Raise 403 if the caller does not have the PayrollBP role."""
    effective_user = user_context["effective_user"]
    roles = sqlhelper.get_user_roles(effective_user["UserId"])
    if "PayrollBP" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="PayrollBP role required",
        )
    return user_context


@router.get("/api/payroll/employee-pay", response_model=EmployeePayResponse)
async def get_employee_pay(
    user_id: int = Query(..., description="Award Nomination UserId of the employee to look up"),
    year:    int = Query(..., ge=2000, le=2100),
    month:   int = Query(..., ge=1,    le=12),
    user_context: dict = Depends(get_current_user_with_impersonation),
):
    """
    Return cycled and off-cycle payroll entries for an employee for a given month.

    Caller must have the PayrollBP role.  The backend resolves the employee's
    UPN and tenant_id, then forwards the request to the payroll-broker which
    routes to the correct provider (Gusto, Workday, …).
    """
    _require_payroll_bp(user_context)

    caller_tenant_id = user_context["effective_user"]["TenantId"]

    # Resolve the target employee — must belong to the same tenant
    emp_row = sqlhelper.get_user_by_id(user_id)
    if not emp_row:
        raise HTTPException(status_code=404, detail="Employee not found")

    # emp_row: (UserId, userPrincipalName, FirstName, LastName, Title, ManagerId)
    upn       = emp_row[1]
    tenant_id = caller_tenant_id   # broker scopes by tenant_id

    if not _BROKER_URL:
        raise HTTPException(
            status_code=503,
            detail="Payroll broker not configured (PAYROLL_BROKER_INTERNAL_URL missing)",
        )

    broker_endpoint = f"{_BROKER_URL}/employee-pay"
    try:
        resp = httpx.get(
            broker_endpoint,
            params={
                "tenant_id": tenant_id,
                "upn":       upn,
                "year":      year,
                "month":     month,
            },
            timeout=30,
        )
    except httpx.RequestError as exc:
        logger.error("Payroll broker unreachable: %s", exc)
        raise HTTPException(status_code=503, detail="Payroll broker unreachable")

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=resp.json().get("detail", "Not found"))
    if not resp.is_success:
        logger.error(
            "Payroll broker error status=%d body=%s", resp.status_code, resp.text[:400]
        )
        raise HTTPException(status_code=502, detail="Payroll broker returned an error")

    data = resp.json()
    return EmployeePayResponse(
        upn=upn,
        year=year,
        month=month,
        entries=[PayrollEntry(**e) for e in data.get("entries", [])],
    )
