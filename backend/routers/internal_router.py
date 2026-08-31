"""
routers/internal_router.py
==========================
Internal service-to-service endpoints — not exposed through Front Door.
Authentication is via shared secrets in request headers.

Routes
------
POST /api/internal/refresh-fraud-model      — fraud-analytics-job cache refresh
POST /api/internal/checkPendingHRBPReview   — Logic App SLA breach check
"""

import logging
import os

from fastapi import APIRouter, Header, HTTPException

import utils.sqlhelper2 as sqlhelper
from utils.rf_model_cache import rf_model_cache
from utils.service_bus_publisher import publish_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal"])

# Shared secret for the fraud-analytics-job post-training callback.
# Set via Key Vault secret FRAUD-ANALYTICS-JOB-WEBHOOK-SECRET → env var.
# Omit (leave blank) in local dev to skip auth.
_FRAUD_ANALYTICS_JOB_WEBHOOK_SECRET = os.getenv("FRAUD_ANALYTICS_JOB_WEBHOOK_SECRET", "")

# SLA threshold — how many hours a nomination may sit in PendingHRBPReview
# before the Logic App callback triggers escalation.
_HRBP_SLA_HOURS: int = int(os.getenv("HRBP_SLA_HOURS", "72"))

# Shared secret for the Logic App SLA-check callback.
# Set via Key Vault secret HRBP-SLA-WEBHOOK-SECRET → env var HRBP_SLA_WEBHOOK_SECRET.
_HRBP_SLA_WEBHOOK_SECRET: str = os.getenv("HRBP_SLA_WEBHOOK_SECRET", "")


@router.post("/api/internal/refresh-fraud-model")
async def internal_refresh_fraud_model(x_internal_key: str = Header(default="")):
    """
    Internal endpoint — called by the fraud-analytics-job after it uploads
    fresh model pkls to blob storage. Forces an immediate cache refresh so
    all in-memory models are replaced without waiting for the idle-TTL eviction cycle.

    Auth: shared secret in X-Internal-Key header (FRAUD_ANALYTICS_JOB_WEBHOOK_SECRET).
    Not exposed through Front Door — job calls the Container App's internal FQDN.
    """
    if _FRAUD_ANALYTICS_JOB_WEBHOOK_SECRET and x_internal_key != _FRAUD_ANALYTICS_JOB_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid internal key")

    updated = rf_model_cache.check_for_updates()
    tenant_summaries = {
        tid: str(entry.model["training_date"]) if entry.model else "not loaded"
        for tid, entry in rf_model_cache.loaded_tenants().items()
    }
    logger.info(
        "internal_refresh_fraud_model: updated=%s tenants=%s",
        updated, list(tenant_summaries.keys()),
    )
    return {
        "status":        "success" if updated else "no_cached_models",
        "updated":       updated,
        "tenant_models": tenant_summaries,
        "message": (
            "Cache refreshed with latest blob models."
            if updated
            else "No models were cached — fresh pkls will be streamed on next request."
        ),
    }


@router.post("/api/internal/checkPendingHRBPReview")
async def internal_check_hrbp_sla(x_internal_key: str = Header(default="")):
    """
    Internal SLA-check endpoint — called daily by the Logic App la-award-hrbp-sla-{env}.

    Finds all nominations that have been in PendingHRBPReview longer than
    HRBP_SLA_HOURS and publishes a nomination.hrbp-sla-breach event for each.
    The auxiliary service emails the admin team.

    Auth: shared secret in X-Internal-Key header (HRBP_SLA_WEBHOOK_SECRET).
    Skip auth (dev only) when env var is not configured.
    """
    if _HRBP_SLA_WEBHOOK_SECRET and x_internal_key != _HRBP_SLA_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid internal key")

    breached = sqlhelper.get_sla_breached_nominations(_HRBP_SLA_HOURS)
    published = 0
    for nom in breached:
        try:
            await publish_event(
                "nomination.hrbp-sla-breach",
                nom["nomination_id"],
                extra={
                    "tenant_id":       nom["tenant_id"],
                    "risk_level":      nom["risk_level"],
                    "nomination_date": nom["nomination_date"],
                    "sla_hours":       _HRBP_SLA_HOURS,
                },
            )
            published += 1
        except Exception as exc:
            logger.error(
                "Failed to publish sla-breach event for nomination %d: %s",
                nom["nomination_id"], exc,
            )

    logger.info(
        "internal_check_hrbp_sla: found %d breach(es), published %d event(s).",
        len(breached), published,
    )
    return {
        "status":           "ok",
        "sla_hours":        _HRBP_SLA_HOURS,
        "breaches_found":   len(breached),
        "events_published": published,
    }
