"""
handler.py — nomination.submitted event handler
================================================
Orchestrates the full fraud assessment lifecycle for a single nomination:
  1. Idempotency check (dbo.ProcessedEvents)
  2. Load nomination details from DB
  3. Run fraud_check.assess()
  4. Persist scores + update nomination status
  5. Re-publish nomination.created or nomination.fraud-flagged

All ML logic lives in fraud_check.py. This file is pure orchestration.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import db
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
    logger.info("Fraud check starting",
                extra={"nomination_id": nomination_id, "tenant_id": tenant_id})

    # ── Assess ────────────────────────────────────────────────────────────────
    result = fraud_check.assess(details, tenant_id)

    if not result["model_available"]:
        logger.warning(
            "No fraud model for tenant %d — routing as clean", tenant_id,
            extra={"nomination_id": nomination_id},
        )
        db.set_nomination_status(nomination_id, "Pending")
        service_bus_publisher.publish_event("nomination.created", nomination_id)
        db.update_processed_event_result(message_id, "success")
        return

    logger.info(
        "Fraud assessment complete",
        extra={
            "nomination_id": nomination_id,
            "fraud_score":   result["fraud_score"],
            "risk_level":    result["risk_level"],
            "warning_flags": result["warning_flags"],
        },
    )

    # ── Persist P2P fraud score ───────────────────────────────────────────────
    db.save_p2p_fraud_score(
        nomination_id=nomination_id,
        fraud_score=result["fraud_score"],
        risk_level=result["risk_level"],
        warning_flags=", ".join(result["warning_flags"]),
    )

    # ── Route ─────────────────────────────────────────────────────────────────
    if result["flagged"]:
        db.save_hrbp_fraud_flags(
            nomination_id=nomination_id,
            fraud_score=result["fraud_score"],
            fraud_probability=result["fraud_prob"],
            risk_level=result["risk_level"],
            warning_flags=", ".join(result["warning_flags"]),
            top_features_json=None,
            feature_summary_json=json.dumps({
                "fraud_score":       result["fraud_score"],
                "fraud_probability": result["fraud_prob"],
                "risk_level":        result["risk_level"],
            }),
        )
        db.set_nomination_status(nomination_id, "PendingHRBPReview")
        service_bus_publisher.publish_event(
            "nomination.fraud-flagged", nomination_id,
            extra={"risk_level": result["risk_level"]},
        )
        logger.info("Nomination flagged for HRBP review",
                    extra={"nomination_id": nomination_id,
                           "risk_level": result["risk_level"]})
    else:
        db.set_nomination_status(nomination_id, "Pending")
        service_bus_publisher.publish_event("nomination.created", nomination_id)
        logger.info("Nomination routed to manager for approval",
                    extra={"nomination_id": nomination_id,
                           "fraud_score": result["fraud_score"]})

    db.update_processed_event_result(message_id, "success")
