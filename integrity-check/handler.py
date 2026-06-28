"""
handler.py — nomination.submitted event handler
================================================
Orchestrates the full fraud assessment lifecycle for a single nomination:
  1. Idempotency check (dbo.ProcessedEvents)
  2. Load nomination details + tenant desc_check_config from DB
  3. Run description_check (Check A + Check B) — pre-ML quality gates
       Check A fail  → auto-reject + nomination.description-rejected event
       Check B flag  → accumulate into warning_flags, continue to ML
  4. Run fraud_check.assess() — ML behavioural fraud model
  5. Persist scores + update nomination status
  6. Re-publish nomination.created or nomination.fraud-flagged

All ML logic lives in fraud_check.py.
All description quality logic lives in description_check.py.
This file is pure orchestration.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import db
import description_check
import fraud_check
import service_bus_publisher

logger = logging.getLogger("integrity_check.handler")


def handle(message_id: str, payload: dict) -> None:
    """
    Process a nomination.submitted message end-to-end.

    Args:
        message_id: Service Bus message ID — used as idempotency key.
        payload:    Decoded JSON body, must contain 'nomination_id'.
    """
    event_type    = payload.get("event_type")
    nomination_id = payload.get("nomination_id")

    if not nomination_id:
        raise ValueError(f"Missing nomination_id in payload: {payload}")

    # ── Idempotency ───────────────────────────────────────────────────────────
    already_done = db.claim_message(
        message_id=message_id,
        event_type=event_type or "nomination.submitted",
        nomination_id=nomination_id,
        processed_at=datetime.now(timezone.utc),
    )
    if already_done:
        logger.info("Already processed — skipping",
                    extra={"message_id": message_id, "nomination_id": nomination_id})
        return

    # ── Load nomination ───────────────────────────────────────────────────────
    details = db.get_nomination_details(nomination_id)
    if not details:
        raise ValueError(f"Nomination {nomination_id} not found in DB")

    tenant_id = details["tenant_id"]
    logger.info("Integrity check starting",
                extra={"nomination_id": nomination_id, "tenant_id": tenant_id})

    # ── Load per-tenant description check config ──────────────────────────────
    desc_config = db.get_tenant_desc_check_config(tenant_id)

    # ── Description quality checks (pre-ML) ───────────────────────────────────
    desc_result = description_check.check(
        description=details.get("description") or "",
        category_description=details.get("category_description"),
        nominator_id=details["nominator_id"],
        config=desc_config,
        nomination_id=nomination_id,
        amount=details.get("amount"),       # passed to Check C for amount justification
    )

    if desc_result.action == "reject":
        # Check A failed — auto-reject, skip ML entirely.
        logger.info(
            "Description check rejected nomination",
            extra={
                "nomination_id": nomination_id,
                "check":         desc_result.check,
                "reason":        desc_result.reason,
            },
        )
        db.reject_nomination(nomination_id, reason=desc_result.reason, actor="Fraud Detection")
        service_bus_publisher.publish_event(
            "nomination.description-rejected", nomination_id,
            extra={
                "check":  desc_result.check,
                "reason": desc_result.reason,
            },
        )
        db.update_processed_event_result(message_id, "success")
        return

    # Accumulate Check B flag (if any) into the warning flags list that the
    # ML model and HRBP will see.  A duplicate-description flag is labelled
    # distinctly so the HRBP email can surface it separately from ML signals.
    pre_ml_flags: list[str] = []
    if desc_result.action == "flag":
        pre_ml_flags.append(f"[Description] {desc_result.reason}")
        logger.info(
            "Description check flagged nomination — continuing to ML",
            extra={"nomination_id": nomination_id, "check": desc_result.check},
        )
    else:
        logger.info(
            "Description checks passed",
            extra={"nomination_id": nomination_id},
        )

    # ── ML fraud assessment ───────────────────────────────────────────────────
    result = fraud_check.assess(details, tenant_id)

    if not result["model_available"]:
        # No trained model yet for this tenant.  Description flags are still
        # actionable even without ML — route to HRBP review if any exist,
        # otherwise pass through to manager approval as normal.
        if pre_ml_flags:
            logger.warning(
                "No fraud model for tenant %d — routing to HRBP review due to description flags",
                tenant_id,
                extra={"nomination_id": nomination_id, "pre_ml_flags": pre_ml_flags},
            )
            db.save_hrbp_fraud_flags(
                nomination_id=nomination_id,
                fraud_score=0,
                fraud_probability=0.0,
                risk_level="UNKNOWN",
                warning_flags=", ".join(pre_ml_flags),
                shap_explanations_json=None,
                feature_summary_json=json.dumps({
                    "fraud_score":       0,
                    "fraud_probability": 0.0,
                    "risk_level":        "UNKNOWN",
                    "description_flags": pre_ml_flags,
                }),
            )
            db.set_nomination_status(nomination_id, "PendingHRBPReview")
            service_bus_publisher.publish_event(
                "nomination.fraud-flagged", nomination_id,
                extra={"risk_level": "UNKNOWN"},
            )
        else:
            logger.warning(
                "No fraud model for tenant %d — routing as clean", tenant_id,
                extra={"nomination_id": nomination_id},
            )
            db.set_nomination_status(nomination_id, "Pending")
            service_bus_publisher.publish_event("nomination.created", nomination_id)
        db.update_processed_event_result(message_id, "success")
        return

    # Merge pre-ML description flags with ML warning flags.
    all_flags = pre_ml_flags + result["warning_flags"]

    logger.info(
        "Fraud assessment complete",
        extra={
            "nomination_id":   nomination_id,
            "fraud_score":     result["fraud_score"],
            "risk_level":      result["risk_level"],
            "warning_flags":   all_flags,
            "shap_available":  bool(result["shap_explanations"]),
        },
    )

    # ── Persist P2P fraud score ───────────────────────────────────────────────
    db.save_p2p_fraud_score(
        nomination_id=nomination_id,
        fraud_score=result["fraud_score"],
        risk_level=result["risk_level"],
        warning_flags=", ".join(all_flags),
    )

    # ── Route ─────────────────────────────────────────────────────────────────
    # CRITICAL  → auto-reject immediately; LLM-generated explanation goes into
    #             RejectionReason so the nominator sees it in My Nominations.
    #             No HRBP queue entry — score is too high to warrant manual review.
    #
    # MEDIUM / HIGH (or any description flag) → PendingHRBPReview with full
    #             SHAP breakdown stored for the reviewer.
    #
    # LOW / NONE → pass through to manager approval as normal.

    risk_level = result["risk_level"]
    shap_json  = json.dumps(result["shap_explanations"]) if result["shap_explanations"] else None
    feature_summary = json.dumps({
        "fraud_score":       result["fraud_score"],
        "fraud_probability": result["fraud_prob"],
        "risk_level":        risk_level,
        "description_flags": pre_ml_flags,
    })

    if risk_level == "CRITICAL":
        explanation = (
            result["fraud_explanation"]
            or "Your nomination was automatically declined because our fraud prevention "
               "system detected unusual patterns in this submission. Please contact your "
               "HR administrator if you believe this is an error."
        )
        db.reject_nomination(
            nomination_id, reason=explanation, actor="Fraud Detection"
        )
        # Persist SHAP data even though no HRBP will review this nomination —
        # analytics and audit trails read from HRBP_FraudFlags for all risk levels.
        db.save_hrbp_fraud_flags(
            nomination_id=nomination_id,
            fraud_score=result["fraud_score"],
            fraud_probability=result["fraud_prob"],
            risk_level=risk_level,
            warning_flags=", ".join(all_flags),
            shap_explanations_json=shap_json,
            feature_summary_json=feature_summary,
        )
        service_bus_publisher.publish_event(
            "nomination.fraud-flagged", nomination_id,
            extra={"risk_level": risk_level, "auto_rejected": True},
        )
        logger.info(
            "Nomination auto-rejected (CRITICAL fraud score)",
            extra={
                "nomination_id": nomination_id,
                "fraud_score":   result["fraud_score"],
            },
        )

    elif result["flagged"] or bool(pre_ml_flags):
        db.save_hrbp_fraud_flags(
            nomination_id=nomination_id,
            fraud_score=result["fraud_score"],
            fraud_probability=result["fraud_prob"],
            risk_level=risk_level,
            warning_flags=", ".join(all_flags),
            shap_explanations_json=shap_json,
            feature_summary_json=feature_summary,
        )
        db.set_nomination_status(nomination_id, "PendingHRBPReview")
        service_bus_publisher.publish_event(
            "nomination.fraud-flagged", nomination_id,
            extra={"risk_level": risk_level},
        )
        logger.info(
            "Nomination flagged for HRBP review",
            extra={
                "nomination_id": nomination_id,
                "risk_level":    risk_level,
                "pre_ml_flags":  pre_ml_flags,
            },
        )

    else:
        db.set_nomination_status(nomination_id, "Pending")
        service_bus_publisher.publish_event("nomination.created", nomination_id)
        logger.info(
            "Nomination routed to manager for approval",
            extra={"nomination_id": nomination_id, "fraud_score": result["fraud_score"]},
        )

    db.update_processed_event_result(message_id, "success")
