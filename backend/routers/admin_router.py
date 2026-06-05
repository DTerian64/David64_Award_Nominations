"""
routers/admin_router.py
=======================
Admin-only management endpoints (AWard_Nomination_Admin role required).

Routes
------
GET  /api/admin/audit-logs                      — impersonation audit log
POST /api/admin/refresh-fraud-model             — manually pull latest model from blob
GET  /api/admin/fraud-model-info                — inspect loaded per-tenant models
GET  /api/admin/nominations/{id}/logs           — Log Analytics trace for a nomination
"""

import logging
import os
from datetime import timedelta
from typing import List

from azure.identity import DefaultAzureCredential
from azure.monitor.query import LogsQueryClient, LogsQueryStatus
from fastapi import APIRouter, Depends, HTTPException, Query, status

import fraud_ml
import utils.sqlhelper2 as sqlhelper
from auth import get_current_user, is_admin, require_role
from routers.schemas import AuditLog

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])

# Log Analytics workspace ID — set by Terraform as a plain env var on the backend container.
# Required only for the nomination logs endpoint; other admin routes don't need it.
_LOG_ANALYTICS_WORKSPACE_ID = os.getenv("LOG_ANALYTICS_WORKSPACE_ID", "")

# Same pattern as service_bus_publisher.py — must be explicit on ACA so IMDS
# resolves the correct user-assigned MI rather than looking for a system-assigned one.
_MI_CLIENT_ID = os.getenv("MI_CLIENT_ID") or None


@router.get("/api/admin/audit-logs", response_model=List[AuditLog])
async def get_audit_logs(
    limit: int = 100,
    current_user: dict = Depends(get_current_user),
):
    """Get impersonation audit logs (AWard_Nomination_Admin only)"""
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AWard_Nomination_Admin access required"
        )

    rows = sqlhelper.get_audit_logs(limit)

    logs = []
    for row in rows:
        logs.append(AuditLog(
            AuditId=row[0],
            Timestamp=row[1],
            AdminUPN=row[2],
            ImpersonatedUPN=row[3],
            Action=row[4],
            Details=row[5],
            IpAddress=row[6]
        ))

    return logs


@router.post("/api/admin/refresh-fraud-model")
async def refresh_fraud_model(current_user: dict = Depends(require_role("AWard_Nomination_Admin"))):
    """
    Manually refresh the fraud detection model from Azure Blob Storage (Admin only).
    Checks if there's a newer version in blob storage and downloads it if available.
    """
    try:
        updated = fraud_ml.refresh_model()

        tenant_summaries = {
            tid: str(entry.model['training_date']) if entry.model else "not loaded"
            for tid, entry in fraud_ml.fraud_detector.loaded_tenants().items()
        }

        return {
            "status": "success",
            "message": "Fraud detection models updated successfully" if updated else "Models already up to date",
            "updated": updated,
            "tenant_models": tenant_summaries,
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to refresh model: {str(e)}"
        )


@router.get("/api/admin/fraud-model-info")
async def get_fraud_model_info(current_user: dict = Depends(require_role("AWard_Nomination_Admin"))):
    """Get information about the currently loaded fraud detection model (Admin only)"""
    loaded = fraud_ml.fraud_detector.loaded_tenants()
    if not any(entry.model is not None for entry in loaded.values()):
        return {
            "status": "not_loaded",
            "message": "No fraud detection models are currently in cache",
        }

    return {
        "status": "loaded",
        "tenant_models": {
            tid: (
                {
                    "model_trained":    str(entry.model['training_date']),
                    "training_samples": entry.model.get('training_samples'),
                    "auc":              entry.model.get('auc'),
                    "feature_count":    len(entry.model['feature_columns']),
                    "features":         entry.model['feature_columns'],
                }
                if entry.model else {"status": "not_loaded"}
            )
            for tid, entry in loaded.items()
        },
    }


@router.get("/api/admin/nominations/{nomination_id}/logs")
async def get_nomination_logs(
    nomination_id: int,
    current_user: dict = Depends(get_current_user),
):
    """
    Return the Log Analytics trace for a single nomination (Admin only).

    Queries ContainerAppConsoleLogs_CL for all App_Log entries that mention
    the given nomination_id across the backend, integrity-check, and auxiliary
    containers. Results are sorted by the inner JSON timestamp (actual emission
    time, not Log Analytics ingestion time).

    Fixed 7-day lookback — covers any realistic support scenario within
    Log Analytics' 30-day retention window.

    Note: Log Analytics has a ~2 minute ingestion delay. Logs for nominations
    submitted in the last 2 minutes may not appear yet.
    """
    if not is_admin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="AWard_Nomination_Admin access required")

    if not _LOG_ANALYTICS_WORKSPACE_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LOG_ANALYTICS_WORKSPACE_ID is not configured on this environment",
        )

    kql = f"""
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(7d)
| where Log_s has "App_Log:"
| where Log_s has "{nomination_id}"
| extend d = parse_json(Log_s)
| extend
    LogTime      = todatetime(d.timestamp),
    PacificTime  = datetime_utc_to_local(todatetime(d.timestamp), 'US/Pacific'),
    Level        = tostring(d.level),
    Service      = ContainerAppName_s,
    Logger       = tostring(d.logger),
    Message      = tostring(d.message),
    NominationId = toint(d.nomination_id)
| where NominationId == {nomination_id}
| project PacificTime, LogTime, Level, Service, Logger, Message
| order by LogTime asc
"""

    try:
        credential = DefaultAzureCredential(managed_identity_client_id=_MI_CLIENT_ID)
        client = LogsQueryClient(credential)

        response = client.query_workspace(
            workspace_id=_LOG_ANALYTICS_WORKSPACE_ID,
            query=kql,
            timespan=timedelta(days=7),
        )

        if response.status != LogsQueryStatus.SUCCESS:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Log Analytics query failed: {response.partial_error}",
            )

        logs = []
        if response.tables:
            table = response.tables[0]
            col_names = [col.name for col in table.columns]
            for row in table.rows:
                entry = dict(zip(col_names, row))
                logs.append({
                    "time":    str(entry.get("PacificTime", "")),
                    "level":   entry.get("Level", ""),
                    "service": entry.get("Service", ""),
                    "logger":  entry.get("Logger", ""),
                    "message": entry.get("Message", ""),
                })

        logger.info(
            "Admin fetched nomination logs",
            extra={"nomination_id": nomination_id, "log_count": len(logs)},
        )

        return {
            "nomination_id":  nomination_id,
            "log_count":      len(logs),
            "logs":           logs,
            "ingestion_note": "Log Analytics has a ~2 min ingestion delay. Recent nominations may show incomplete results.",
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to query nomination logs", extra={"nomination_id": nomination_id})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Log query failed: {str(exc)}",
        )
