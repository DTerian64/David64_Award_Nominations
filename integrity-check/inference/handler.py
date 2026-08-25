"""
handler.py — nomination.submitted event handler
================================================
Orchestrates the full fraud assessment lifecycle for a single nomination:
  1. Idempotency check (dbo.ProcessedEvents)
  2. Load nomination details + tenant desc_check_config from DB
  3. Run description_check (Check A + Check B) and retain its evidence
  4. Run RF, graph analytics, and GNN as separate component scorers
  5. Persist each component score and dbo.FraudDecisionResults
  6. Apply the explicit rules-based routing policy using all available evidence
  7. Re-publish nomination.created, nomination.fraud-flagged, or rejection

Component logic lives in random_forest_check.py, graph_check.py, and gnn_check.py.
The component-result fusion policy lives in result_fusion.py.
All description quality logic lives in description_check.py.
This file is pure orchestration.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from . import description_check
from . import gnn_check
from . import graph_check
from . import random_forest_check
from . import result_fusion
from utils import db
from utils import service_bus_publisher

logger = logging.getLogger("integrity_check.handler")

# RejectionActor values written by this service.
#
# These must stay distinct. modeling.train_rf_model.load_data() excludes description
# rejections from the training population (a failed quality gate is not a fraud
# signal) but must NOT exclude ML auto-rejects. Until 2026-08 both paths wrote
# the literal "Fraud Detection", so the exclusion silently discarded every
# CRITICAL auto-reject as if it were a typo in a description.
ACTOR_DESCRIPTION_CHECK = "Fraud Detection (Description)"   # Check A quality gate
ACTOR_FRAUD_ML          = "Fraud Detection"                 # CRITICAL ML auto-reject


def _component_summary(result: dict) -> dict:
    """JSON-safe audit view without model internals or large arrays."""
    keys = (
        "model_available", "availability_status", "unavailable_reason",
        "unavailable_detail", "last_attempt_status", "last_attempt_at",
        "last_successful_at", "registry_serving_status", "status_run_id",
        "fraud_score", "fraud_prob",
        "risk_level", "source_severity", "warning_flags", "flagged", "model_version",
        "embedding_as_of", "snapshot_as_of", "affected_user_ids",
    )
    return {key: result.get(key) for key in keys if key in result}


def _select_route(desc_result, decision: dict) -> dict:
    """Return the final rules-based route after every assessment is persisted."""
    if desc_result.action == "reject":
        return {
            "route": "REJECT_DESCRIPTION",
            "target_status": "Rejected",
            "routing_rule": "check_a_incoherent_reject",
        }

    if decision["decision_available"] and decision["risk_level"] == "CRITICAL":
        return {
            "route": "REJECT_FRAUD",
            "target_status": "Rejected",
            "routing_rule": "critical_component_risk_reject",
        }

    if desc_result.action == "flag" or decision["flagged"]:
        concern_source = (
            "description_and_fraud"
            if desc_result.action == "flag" and decision["flagged"]
            else "description"
            if desc_result.action == "flag"
            else "fraud"
        )
        return {
            "route": "HRBP_REVIEW",
            "target_status": "PendingHRBPReview",
            "routing_rule": f"{concern_source}_concern_hrbp",
        }

    return {
        "route": "MANAGER_APPROVAL",
        "target_status": "Pending",
        "routing_rule": (
            "risk_below_review_threshold"
            if decision["decision_available"]
            else "no_available_fraud_opinion"
        ),
    }


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
        amount=details.get("amount"),       # Check A LLM amount justification
    )

    # Retain all description evidence for the final routing policy. Even a hard
    # description rejection no longer short-circuits component assessment.
    pre_ml_flags: list[str] = []
    if desc_result.action in ("flag", "reject"):
        pre_ml_flags.append(f"[Description] {desc_result.reason}")
        logger.info(
            "Description check produced routing evidence — continuing to component assessments",
            extra={
                "nomination_id": nomination_id,
                "check": desc_result.check,
                "action": desc_result.action,
                "reason": desc_result.reason,
            },
        )
    else:
        logger.info(
            "Description checks passed",
            extra={"nomination_id": nomination_id},
        )

    # ── Independent component assessments ────────────────────────────────────
    try:
        component_statuses = db.get_integrity_component_statuses(tenant_id)
    except Exception as exc:
        # Status provenance must enrich scoring, never prevent it. This also
        # keeps rolling deployment safe while migration 0045 is being applied.
        logger.warning(
            "Component availability registry could not be loaded: %s",
            exc,
            extra={"nomination_id": nomination_id, "tenant_id": tenant_id},
        )
        component_statuses = {}

    logger.info(
        "RF assessment starting",
        extra={"nomination_id": nomination_id, "tenant_id": tenant_id},
    )
    rf_result = random_forest_check.assess(details, tenant_id, component_statuses.get("RF"))
    logger.info(
        "RF assessment completed",
        extra={
            "nomination_id": nomination_id,
            "model_available": rf_result["model_available"],
            "unavailable_reason": rf_result.get("unavailable_reason"),
            "unavailable_detail": rf_result.get("unavailable_detail"),
            "last_attempt_status": rf_result.get("last_attempt_status"),
            "fraud_score": rf_result.get("fraud_score"),
            "risk_level": rf_result.get("risk_level"),
        },
    )

    logger.info(
        "Graph Analytics assessment starting",
        extra={"nomination_id": nomination_id, "tenant_id": tenant_id},
    )
    graph_result = graph_check.assess_graph(
        details, tenant_id, component_statuses.get("GRAPH")
    )
    logger.info(
        "Graph Analytics assessment completed",
        extra={
            "nomination_id": nomination_id,
            "model_available": graph_result["model_available"],
            "unavailable_reason": graph_result.get("unavailable_reason"),
            "unavailable_detail": graph_result.get("unavailable_detail"),
            "last_attempt_status": graph_result.get("last_attempt_status"),
            "fraud_score": graph_result.get("fraud_score"),
            "risk_level": graph_result.get("risk_level"),
        },
    )

    logger.info(
        "GNN assessment starting",
        extra={"nomination_id": nomination_id, "tenant_id": tenant_id},
    )
    gnn_result = gnn_check.assess_gnn(details, tenant_id, component_statuses.get("GNN"))
    logger.info(
        "GNN assessment completed",
        extra={
            "nomination_id": nomination_id,
            "model_available": gnn_result["model_available"],
            "unavailable_reason": gnn_result.get("unavailable_reason"),
            "unavailable_detail": gnn_result.get("unavailable_detail"),
            "last_attempt_status": gnn_result.get("last_attempt_status"),
            "fraud_score": gnn_result.get("fraud_score"),
            "risk_level": gnn_result.get("risk_level"),
            "model_version": gnn_result.get("model_version"),
        },
    )

    # Persist every available opinion in its own component table. Every available
    # component participates in fusion; unavailable means no opinion.
    if rf_result["model_available"]:
        rf_flags = result_fusion.component_flags("RF", rf_result)
        db.save_p2p_fraud_score(
            nomination_id=nomination_id,
            fraud_score=rf_result["fraud_score"],
            risk_level=rf_result["risk_level"],
            warning_flags=", ".join(rf_flags),
        )

    if graph_result["model_available"]:
        db.save_graph_fraud_score(
            nomination_id=nomination_id,
            graph_score=graph_result["fraud_score"],
            risk_level=graph_result["risk_level"],
            graph_flags=", ".join(graph_result["warning_flags"]) or None,
            snapshot_as_of=graph_result["snapshot_as_of"],
        )

    if gnn_result["model_available"]:
        db.save_gnn_fraud_score(
            nomination_id=nomination_id,
            fraud_score=gnn_result["fraud_score"],
            fraud_probability=gnn_result["fraud_prob"],
            risk_level=gnn_result["risk_level"],
            warning_flags=", ".join(gnn_result["warning_flags"]) or None,
            model_version=gnn_result["model_version"],
            embedding_as_of=gnn_result["embedding_as_of"],
        )

    decision = result_fusion.combine(rf_result, graph_result, gnn_result)
    db.save_fraud_decision_result(
        nomination_id=nomination_id,
        policy_version=decision["policy_version"],
        rf_result=rf_result,
        graph_result=graph_result,
        gnn_result=gnn_result,
        decision=decision,
    )
    logger.info(
        "FraudDecisionResults persisted",
        extra={
            "nomination_id": nomination_id,
            "policy_version": decision["policy_version"],
            "rf_available": rf_result["model_available"],
            "rf_unavailable_reason": rf_result.get("unavailable_reason"),
            "graph_available": graph_result["model_available"],
            "graph_unavailable_reason": graph_result.get("unavailable_reason"),
            "gnn_available": gnn_result["model_available"],
            "gnn_unavailable_reason": gnn_result.get("unavailable_reason"),
            "final_score": decision["final_score"],
            "final_risk": decision["risk_level"],
            "decisive_models": decision["decisive_models"],
        },
    )

    all_flags = pre_ml_flags + decision["warning_flags"]

    logger.info(
        "Three-component fraud assessment complete",
        extra={
            "nomination_id": nomination_id,
            "rf_risk": rf_result["risk_level"] if rf_result["model_available"] else "UNAVAILABLE",
            "graph_risk": graph_result["risk_level"] if graph_result["model_available"] else "UNAVAILABLE",
            "gnn_risk": gnn_result["risk_level"] if gnn_result["model_available"] else "UNAVAILABLE",
            "final_risk": decision["risk_level"],
            "decisive_models": decision["decisive_models"],
            "warning_flags": all_flags,
        },
    )

    # ── Final rules-based route (only after all persistence above) ────────────

    risk_level = decision["risk_level"]
    shap_json = (json.dumps(rf_result["shap_explanations"])
                 if rf_result.get("shap_explanations") else None)
    feature_summary = json.dumps({
        "policy_version": decision["policy_version"],
        "final_score": decision["final_score"],
        "final_risk_level": risk_level,
        "participating_models": decision["participating_models"],
        "decisive_models": decision["decisive_models"],
        "rf": _component_summary(rf_result),
        "graph": _component_summary(graph_result),
        "gnn": _component_summary(gnn_result),
        "description_action": desc_result.action,
        "description_check": desc_result.check,
        "description_reason": desc_result.reason,
        "description_flags": pre_ml_flags,
    }, default=str)
    decision_probability = float(decision["decision_probability"] or 0.0)
    route_decision = _select_route(desc_result, decision)

    logger.info(
        "Rules-based routing decision",
        extra={
            "nomination_id": nomination_id,
            "route": route_decision["route"],
            "target_status": route_decision["target_status"],
            "routing_rule": route_decision["routing_rule"],
            "description_action": desc_result.action,
            "fraud_decision_available": decision["decision_available"],
            "final_risk": risk_level,
            "final_score": decision["final_score"],
            "warning_flags": all_flags,
        },
    )

    if route_decision["route"] == "REJECT_DESCRIPTION":
        db.reject_nomination(
            nomination_id,
            reason=desc_result.reason,
            actor=ACTOR_DESCRIPTION_CHECK,
        )
        service_bus_publisher.publish_event(
            "nomination.description-rejected",
            nomination_id,
            extra={
                "check": desc_result.check,
                "reason": desc_result.reason,
                "routing_rule": route_decision["routing_rule"],
            },
        )
        logger.info(
            "Nomination rejected by description policy after component assessment",
            extra={
                "nomination_id": nomination_id,
                "check": desc_result.check,
                "reason": desc_result.reason,
                "routing_rule": route_decision["routing_rule"],
            },
        )

    elif route_decision["route"] == "REJECT_FRAUD":
        explanation = (
            rf_result.get("fraud_explanation")
            if "RF" in decision["decisive_models"] else None
        ) or (
            "Your nomination was automatically declined because independent fraud "
            "screening detected an unusually high-risk pattern in this submission. "
            "Please contact your HR administrator if you believe this is an error."
        )
        db.reject_nomination(
            nomination_id, reason=explanation, actor=ACTOR_FRAUD_ML
        )
        # Persist SHAP data even though no HRBP will review this nomination —
        # analytics and audit trails read from HRBP_FraudFlags for all risk levels.
        db.save_hrbp_fraud_flags(
            nomination_id=nomination_id,
            fraud_score=decision["final_score"],
            fraud_probability=decision_probability,
            risk_level=risk_level,
            warning_flags=", ".join(all_flags),
            shap_explanations_json=shap_json,
            feature_summary_json=feature_summary,
        )
        service_bus_publisher.publish_event(
            "nomination.fraud-flagged", nomination_id,
            extra={
                "risk_level": risk_level,
                "auto_rejected": True,
                "routing_rule": route_decision["routing_rule"],
            },
        )
        logger.info(
            "Nomination auto-rejected (CRITICAL fraud score)",
            extra={
                "nomination_id": nomination_id,
                "fraud_score": decision["final_score"],
                "decisive_models": decision["decisive_models"],
                "routing_rule": route_decision["routing_rule"],
            },
        )

    elif route_decision["route"] == "HRBP_REVIEW":
        db.save_hrbp_fraud_flags(
            nomination_id=nomination_id,
            fraud_score=decision["final_score"],
            fraud_probability=decision_probability,
            risk_level=risk_level,
            warning_flags=", ".join(all_flags),
            shap_explanations_json=shap_json,
            feature_summary_json=feature_summary,
        )
        db.set_nomination_status(nomination_id, "PendingHRBPReview")
        service_bus_publisher.publish_event(
            "nomination.fraud-flagged", nomination_id,
            extra={
                "risk_level": risk_level,
                "routing_rule": route_decision["routing_rule"],
            },
        )
        logger.info(
            "Nomination flagged for HRBP review",
            extra={
                "nomination_id": nomination_id,
                "risk_level":    risk_level,
                "decisive_models": decision["decisive_models"],
                "pre_ml_flags":  pre_ml_flags,
                "routing_rule": route_decision["routing_rule"],
            },
        )

    else:
        if not decision["decision_available"]:
            logger.warning(
                "No active fraud component available for tenant %d — routing to manager",
                tenant_id,
                extra={"nomination_id": nomination_id},
            )
        db.set_nomination_status(nomination_id, "Pending")
        service_bus_publisher.publish_event(
            "nomination.created",
            nomination_id,
            extra={"routing_rule": route_decision["routing_rule"]},
        )
        logger.info(
            "Nomination routed to manager for approval",
            extra={
                "nomination_id": nomination_id,
                "fraud_score": decision["final_score"],
                "routing_rule": route_decision["routing_rule"],
            },
        )

    db.update_processed_event_result(message_id, "success")
