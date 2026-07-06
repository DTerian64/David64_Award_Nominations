"""Pydantic schemas for the Payroll Broker API."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class GustoCallbackResponse(BaseModel):
    message: str
    tenant_id: int
    provider_id: int | None = None
    company_id_at_provider: str | None = None


class RipplingCallbackResponse(BaseModel):
    message: str
    tenant_id: int
    provider_id: int | None = None
    company_id_at_provider: str | None = None
