"""
skills/fraud/tools.py
──────────────────────
Tools owned by the fraud skill:
  • get_fraud_model_info — metadata about the trained Random Forest model
"""

from __future__ import annotations

import logging
from typing import Any

import fraud_ml

logger = logging.getLogger(__name__)


# ── Tool implementation ───────────────────────────────────────────────────────

async def _get_fraud_model_info(tenant_id: int = 0) -> dict[str, Any]:
    model_data = fraud_ml.fraud_detector.tenant_models.get(tenant_id)

    if model_data is None:
        logger.warning("tool:get_fraud_model_info — no model for tenant_id=%d", tenant_id)
        return {
            "status":    "unavailable",
            "tenant_id": tenant_id,
            "message": (
                f"No fraud detection model loaded for tenant {tenant_id}. "
                "Run train_fraud_model.py and upload the resulting .pkl to blob storage."
            ),
        }

    fi = model_data.get("feature_importance")
    top_features = (
        fi.head(10)
          .rename(columns={"Feature": "feature", "Importance": "importance"})
          [["feature", "importance"]]
          .to_dict(orient="records")
        if fi is not None else []
    )

    training_date = model_data.get("training_date")
    training_date_str = (
        training_date.strftime("%Y-%m-%d %H:%M:%S")
        if hasattr(training_date, "strftime") else str(training_date)
    )

    logger.info(
        "tool:get_fraud_model_info — tenant=%d auc=%.3f samples=%d",
        tenant_id,
        model_data.get("auc") or 0.0,
        model_data.get("training_samples", 0),
    )
    return {
        "status":           "success",
        "tenant_id":        tenant_id,
        "training_date":    training_date_str,
        "training_samples": model_data.get("training_samples"),
        "auc":              model_data.get("auc"),
        "amount_mean":      model_data.get("amount_mean"),
        "amount_std":       model_data.get("amount_std"),
        "top_features":     top_features,
    }


# ── OpenAI tool schema ────────────────────────────────────────────────────────

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_fraud_model_info",
            "description": (
                "Return metadata about the trained fraud detection model for the current "
                "tenant: training date, sample count, ROC-AUC score, top 10 feature "
                "importances, and the amount mean/std used for z-score baselines. "
                "Use when the user asks about model accuracy, which signals drive fraud "
                "scores, the amount baseline, or needs context to interpret a fraud score. "
                "Do NOT call for general nomination questions — use query_database for those."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
]

IMPLEMENTATIONS = {
    "get_fraud_model_info": _get_fraud_model_info,
}
