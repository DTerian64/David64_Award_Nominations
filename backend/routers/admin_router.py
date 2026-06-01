"""
routers/admin_router.py
=======================
Admin-only management endpoints (AWard_Nomination_Admin role required).

Routes
------
GET  /api/admin/audit-logs          — impersonation audit log
POST /api/admin/refresh-fraud-model — manually pull latest model from blob
GET  /api/admin/fraud-model-info    — inspect loaded per-tenant models
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List

import sqlhelper2 as sqlhelper
import fraud_ml
from auth import get_current_user, require_role, is_admin
from models import AuditLog

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


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
