"""
Handler: nomination.submitted
==============================
Triggered when a new nomination is saved with status 'Submitted'.

Flow
----
1. Load nomination details from DB.
2. Run full fraud assessment (fraud_check.assess).
3. Persist fraud score to dbo.P2P_FraudScores.
4. If flagged: save HRBP snapshot, set PendingHRBPReview, publish fraud-flagged.
   If clean:   set Pending, publish nomination.created.

All ML logic lives in fraud_check.py — this handler is pure orchestration.
"""

from __future__ import annotations

import json
import logging

import db
import fraud_check
import service_bus_publisher

logger = logging.getLogger("auxiliary.handlers.nomination_submitted")


def handle(payload: dict) -> None:
    nomination_id = payload.get("nomination_id")
    if not nomination_id:
        raise ValueError(f"Missing nomination_id in payload: {payload}")

    # 1. Load nomination
    details = db.get_nomination_details(nomination_id)
    if not details:
        raise ValueError(f"Nomination {nomination_id} not found in DB")

    tenant_id = details["tenant_id"]
    logger.info("Fraud check starting",
                extra={"nomination_id": nomination_id, "tenant_id": tenant_id})

    # 2. Assess
    result = fraud_check.assess(details, tenant_id)

    if not result["model_available"]:
        logger.warning(
            "No fraud model for tenant %d — routing as clean", tenant_id,
            extra={"nomination_id": nomination_id},
        )
        db.set_nomination_status(nomination_id, "Pending")
        service_bus_publisher.publish_event("nomination.created", nomination_id)
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

    # 3. Persist fraud score
    db.save_p2p_fraud_score(
        nomination_id=nomination_id,
        fraud_score=result["fraud_score"],
        risk_level=result["risk_level"],
        warning_flags=", ".join(result["warning_flags"]),
    )

    # 4. Route
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
