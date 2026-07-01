"""Health check endpoint — used by AFD origin health probe."""

from fastapi import APIRouter, Response
from routers.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok")


@router.head("/health")
def health_head():
    return Response(status_code=200)
