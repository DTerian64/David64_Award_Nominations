"""Backfill the four-engine IntegrityDecisionResults contract.

Revision ID: 0048
Revises: 0047
Create Date: 2026-08-29

The migration uses the union of the five legacy result tables as its source
population. FraudDecisionResults remains authoritative for nomination-time
availability and the composite decision. Component tables fill older gaps,
while HRBP_FraudFlags supplies probabilities, SHAP evidence, semantic context,
and evidence that the historical route required human review.

Existing IntegrityDecisionResults rows are never changed. This is important
because rows produced by the live v2 dual-write contain richer evidence than a
historical reconstruction can provide.

Downgrade removes rows stamped ``migration:0048``. Do not downgrade after HRBP
has adjudicated a migrated row unless that post-migration review data has first
been backed up.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from alembic import op


revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

_MIGRATION_ACTOR = "migration:0048"
logger = logging.getLogger("alembic.runtime.migration")
_RISK_RANK = {
    "UNKNOWN": -1,
    "NONE": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}
_ALLOWED_ROUTES = {"MANAGER_APPROVAL", "HRBP_REVIEW", "REJECT_SEMANTIC"}
_ALLOWED_SCOPES = {"FRAUD", "SEMANTIC", "FRAUD_AND_SEMANTIC"}
_REQUIRED_TABLES = (
    "P2P_FraudScores",
    "GNN_FraudScores",
    "Graph_FraudScores",
    "HRBP_FraudFlags",
    "FraudDecisionResults",
    "IntegrityDecisionResults",
)


def _table_exists(name: str) -> bool:
    return op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :name"
        ),
        {"name": name},
    ).fetchone() is not None


def _json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _first(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _risk(value: Any, default: str = "UNKNOWN") -> str:
    risk = str(value or default).upper()
    return risk if risk in _RISK_RANK else default


def _number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flags(*values: Any) -> list[str]:
    for value in values:
        if isinstance(value, list):
            normalized = [str(item) for item in value if item]
            if normalized:
                return normalized
            continue
        parsed = _json_list(value)
        if parsed:
            return [str(item) for item in parsed if item]
        if isinstance(value, str) and value.strip():
            return [part.strip() for part in value.split(", ") if part.strip()]
    return []


def _hrbp_flags(row: dict, engine: str) -> list[str]:
    flags = _flags(row.get("WarningFlags"))
    prefix = {"RF": "[RF]", "GRAPH": "[GRAPH]", "GNN": "[GNN]"}[engine]
    matched = [flag for flag in flags if flag.upper().startswith(prefix)]
    if matched:
        return matched
    # Before multi-engine scoring, HRBP_FraudFlags was RF-owned and its flags
    # were not prefixed. Do not assign modern prefixed Graph/GNN findings to RF.
    if engine == "RF":
        return [flag for flag in flags if not flag.startswith("[")]
    return []


def _component_summary(summary: dict, name: str) -> dict:
    value = summary.get(name)
    return value if isinstance(value, dict) else {}


def _component_available(row: dict, component: str, source_present: bool) -> bool:
    if row.get("FdrNominationId") is not None:
        return bool(row.get(f"{component}Available"))
    return source_present


def _common_engine(
    *,
    engine: str,
    available: bool,
    score: Any,
    probability: Any,
    risk_level: Any,
    unavailable_reason: Any,
    unavailable_detail: Any,
    findings: list[str],
    score_derivation: str,
    provenance: dict,
) -> dict:
    risk = _risk(risk_level, "NONE") if available else "UNKNOWN"
    return {
        "schema_version": 1,
        "engine": engine,
        "available": available,
        "status": "AVAILABLE" if available else "UNAVAILABLE",
        "unavailable_reason": None if available else unavailable_reason,
        "unavailable_detail": None if available else unavailable_detail,
        "score": int(score) if available and score is not None else None,
        "model_probability": probability if available else None,
        "score_derivation": score_derivation,
        "score_thresholds": None,
        "risk_level": risk,
        "flagged": available and risk in {"MEDIUM", "HIGH", "CRITICAL"},
        "findings": findings,
        "provenance": provenance,
    }


def _rf_document(row: dict, summary: dict) -> dict:
    source = _component_summary(summary, "rf")
    source_available = _first(
        source.get("available"), source.get("model_available")
    )
    available = _component_available(
        row,
        "Rf",
        bool(source_available)
        if source_available is not None
        else row.get("P2PScoreId") is not None or row.get("HrbpFlagId") is not None,
    )
    top_features = _json_list(row.get("TopFeaturesJson"))
    if not top_features:
        explanation = source.get("explanation")
        top_features = (
            explanation.get("top_features", [])
            if isinstance(explanation, dict)
            else source.get("shap_explanations", [])
        )
    probability = _number(
        _first(source.get("model_probability"), source.get("fraud_prob"))
    )
    # HRBP_FraudFlags predates multi-engine fusion and was originally the RF
    # probability snapshot. Newer three-engine rows retain the exact RF value
    # in FeatureSummaryJson, so this fallback is used only when that is absent.
    if probability is None:
        probability = _number(row.get("HrbpFraudProbability"))
    probability_source = (
        "FeatureSummaryJson"
        if _first(source.get("model_probability"), source.get("fraud_prob"))
        is not None
        else "HRBP_FraudFlags"
        if row.get("HrbpFraudProbability") is not None
        else "NOT_CAPTURED"
    )

    payload = _common_engine(
        engine="RF",
        available=available,
        score=_first(
            row.get("RfScore"), source.get("score"),
            source.get("fraud_score"), row.get("P2PFraudScore"),
            row.get("HrbpFraudScore"),
        ),
        probability=probability,
        risk_level=_first(
            row.get("RfRiskLevel"), source.get("risk_level"),
            row.get("P2PRiskLevel"), row.get("HrbpRiskLevel"),
        ),
        unavailable_reason=row.get("RfUnavailableReasonCode"),
        unavailable_detail=row.get("RfUnavailableReasonDetail"),
        findings=_flags(
            source.get("findings"),
            source.get("warning_flags"),
            row.get("P2PFraudFlags"),
            _hrbp_flags(row, "RF"),
        ),
        score_derivation="legacy_rf_score",
        provenance={
            "migration": _MIGRATION_ACTOR,
            "source_tables": [
                table
                for present, table in (
                    (row.get("FdrNominationId") is not None, "dbo.FraudDecisionResults"),
                    (row.get("P2PScoreId") is not None, "dbo.P2P_FraudScores"),
                    (row.get("HrbpFlagId") is not None, "dbo.HRBP_FraudFlags"),
                )
                if present
            ],
            "score_id": row.get("P2PScoreId"),
            "decision_scored_by": row.get("FdrScoredBy"),
            "scored_at": row.get("P2PCreatedAt"),
        },
    )
    source_explanation = source.get("explanation")
    if not isinstance(source_explanation, dict):
        source_explanation = {}
    payload.update({
        "model_version": source.get("model_version"),
        "probability_source": probability_source,
        "explanation": {
            "shap_status": _first(
                source_explanation.get("shap_status"),
                source.get("shap_status"),
                "COMPLETED" if top_features else "NOT_CAPTURED",
            ),
            "shap_reason": _first(
                source_explanation.get("shap_reason"),
                source.get("shap_reason"),
            ),
            "top_features": top_features,
            "llm_status": _first(
                source_explanation.get("llm_status"),
                source.get("llm_explanation_status"),
                "NOT_CAPTURED",
            ),
            "llm_reason": _first(
                source_explanation.get("llm_reason"),
                source.get("llm_explanation_reason"),
            ),
            "llm_text": _first(
                source_explanation.get("llm_text"),
                source.get("llm_explanation"),
            ),
        },
    })
    return payload


def _graph_document(row: dict, summary: dict) -> dict:
    source = _component_summary(summary, "graph")
    source_available = _first(
        source.get("available"), source.get("model_available")
    )
    available = _component_available(
        row,
        "Graph",
        bool(source_available)
        if source_available is not None
        else row.get("GraphScoreId") is not None,
    )
    payload = _common_engine(
        engine="GRAPH",
        available=available,
        score=_first(row.get("GraphDecisionScore"), source.get("score"), source.get("fraud_score"), row.get("ComponentGraphScore")),
        probability=None,
        risk_level=_first(row.get("GraphRiskLevel"), source.get("risk_level"), row.get("ComponentGraphRiskLevel")),
        unavailable_reason=row.get("GraphUnavailableReasonCode"),
        unavailable_detail=row.get("GraphUnavailableReasonDetail"),
        findings=_flags(
            source.get("findings"),
            source.get("warning_flags"),
            row.get("GraphFlags"),
            _hrbp_flags(row, "GRAPH"),
        ),
        score_derivation="legacy_rules_based_graph_score",
        provenance={
            "migration": _MIGRATION_ACTOR,
            "source_tables": [
                table
                for present, table in (
                    (row.get("FdrNominationId") is not None, "dbo.FraudDecisionResults"),
                    (row.get("GraphScoreId") is not None, "dbo.Graph_FraudScores"),
                )
                if present
            ],
            "score_id": row.get("GraphScoreId"),
            "decision_scored_by": row.get("FdrScoredBy"),
            "scored_by": row.get("GraphScoredBy"),
            "created_at": row.get("GraphCreatedAt"),
            "updated_at": row.get("GraphUpdatedAt"),
        },
    )
    payload["model_probability"] = None
    severity_by_score = {0: "NONE", 25: "LOW", 50: "MEDIUM", 75: "HIGH", 100: "CRITICAL"}
    payload.update({
        "source_severity": _first(
            source.get("source_severity"),
            severity_by_score.get(payload.get("score")),
        ),
        "snapshot_as_of": _first(
            source.get("snapshot_as_of"), row.get("SnapshotAsOfDate")
        ),
        "affected_user_ids": source.get("affected_user_ids", []),
    })
    return payload


def _gnn_document(row: dict, summary: dict) -> dict:
    source = _component_summary(summary, "gnn")
    source_available = _first(
        source.get("available"), source.get("model_available")
    )
    available = _component_available(
        row,
        "Gnn",
        bool(source_available)
        if source_available is not None
        else row.get("GNNScoreId") is not None,
    )
    payload = _common_engine(
        engine="GNN",
        available=available,
        score=_first(row.get("GnnDecisionScore"), source.get("score"), source.get("fraud_score"), row.get("ComponentGnnScore")),
        probability=_number(_first(source.get("model_probability"), source.get("fraud_prob"), row.get("GnnFraudProbability"))),
        risk_level=_first(row.get("GnnRiskLevel"), source.get("risk_level"), row.get("ComponentGnnRiskLevel")),
        unavailable_reason=row.get("GnnUnavailableReasonCode"),
        unavailable_detail=row.get("GnnUnavailableReasonDetail"),
        findings=_flags(
            source.get("findings"),
            source.get("warning_flags"),
            row.get("GnnFraudFlags"),
            _hrbp_flags(row, "GNN"),
        ),
        score_derivation="legacy_gnn_score",
        provenance={
            "migration": _MIGRATION_ACTOR,
            "source_tables": [
                table
                for present, table in (
                    (row.get("FdrNominationId") is not None, "dbo.FraudDecisionResults"),
                    (row.get("GNNScoreId") is not None, "dbo.GNN_FraudScores"),
                )
                if present
            ],
            "score_id": row.get("GNNScoreId"),
            "decision_scored_by": row.get("FdrScoredBy"),
            "scored_by": row.get("GnnScoredBy"),
            "scored_at": row.get("GnnCreatedAt"),
        },
    )
    payload.update({
        "model_version": _first(source.get("model_version"), row.get("GnnModelVersion")),
        "embedding_as_of": _first(source.get("embedding_as_of"), row.get("EmbeddingAsOfDate")),
    })
    return payload


def _semantic_document(summary: dict) -> dict:
    exact = _component_summary(summary, "semantic")
    if exact.get("engine") == "SEMANTIC":
        exact = dict(exact)
        exact["migration"] = {"source": "dbo.HRBP_FraudFlags.FeatureSummaryJson"}
        return exact

    action = summary.get("description_action")
    check = summary.get("description_check")
    reason = summary.get("description_reason")
    captured = any(value is not None for value in (action, check, reason))
    return {
        "schema_version": 1,
        "engine": "SEMANTIC",
        "available": captured,
        "status": "PARTIAL" if captured else "UNAVAILABLE",
        "embedding": {"available": False, "status": "NOT_CAPTURED"},
        "llm": {"available": False, "status": "NOT_CAPTURED"},
        "duplicate_description": {
            "available": False,
            "status": "NOT_CAPTURED",
        },
        "combined_decision": {
            "action": action,
            "checks": str(check).split("|") if check else [],
            "reason": reason,
        },
        "migration": {"source": "dbo.HRBP_FraudFlags.FeatureSummaryJson"},
    }


def _composite(row: dict, engines: dict[str, dict]) -> tuple[int | None, str]:
    if row.get("FdrNominationId") is not None:
        available = any(engines[name]["available"] for name in ("rf", "graph", "gnn"))
        return (
            int(row["FinalScore"]) if available and row.get("FinalScore") is not None else None,
            _risk(row.get("FinalRiskLevel")),
        )
    if row.get("HrbpFlagId") is not None:
        return int(row["HrbpFraudScore"]), _risk(row.get("HrbpRiskLevel"))

    available = [engine for name, engine in engines.items() if name != "semantic" and engine["available"]]
    if not available:
        return None, "UNKNOWN"
    highest_rank = max(_RISK_RANK[engine["risk_level"]] for engine in available)
    decisive = [engine for engine in available if _RISK_RANK[engine["risk_level"]] == highest_rank]
    score = max(int(engine.get("score") or 0) for engine in decisive)
    risk = next(name for name, rank in _RISK_RANK.items() if rank == highest_rank)
    return score, risk


def _human_review(row: dict) -> dict:
    outcome = row.get("HumanReviewOutcome")
    if not outcome and row.get("P2PConfirmedBy") is not None and row.get("P2PIsFraud") is not None:
        outcome = "CONFIRMED_CONCERN" if row["P2PIsFraud"] else "CLEARED_NO_CONCERN"
    if not outcome:
        return {
            "outcome": None,
            "training": None,
            "reason": None,
            "reviewed_by": None,
            "reviewed_at": None,
        }

    expected_training = {
        "CONFIRMED_CONCERN": "FRAUD",
        "CLEARED_NO_CONCERN": "LEGITIMATE",
        "CLEARED_UNSUBSTANTIATED": "EXCLUDED",
        "CONFIRMED_SEMANTIC_CONCERN": "EXCLUDED",
    }[outcome]
    return {
        "outcome": outcome,
        "training": expected_training,
        "reason": _first(
            row.get("ReviewReason"),
            row.get("RejectionReason"),
            "Migrated from legacy human review",
        ),
        "reviewed_by": _first(row.get("ReviewedBy"), row.get("P2PConfirmedBy"), _MIGRATION_ACTOR),
        "reviewed_at": _first(
            row.get("ReviewedAt"),
            row.get("P2PConfirmedAt"),
            row.get("FdrUpdatedAt"),
            row.get("NominationUpdatedAt"),
            datetime.now(timezone.utc).replace(tzinfo=None),
        ),
    }


def _route_and_scope(
    row: dict,
    summary: dict,
    composite_risk: str,
    human: dict,
) -> tuple[str, str, str | None]:
    explicit_route = summary.get("final_route")
    if explicit_route not in _ALLOWED_ROUTES:
        explicit_route = None
    description_action = summary.get("description_action")
    description_concern = description_action == "flag"
    semantic_outcome = human["outcome"] == "CONFIRMED_SEMANTIC_CONCERN"
    fraud_outcome = human["outcome"] in {
        "CONFIRMED_CONCERN",
        "CLEARED_NO_CONCERN",
    }
    fraud_concern = (
        composite_risk in {"MEDIUM", "HIGH", "CRITICAL"}
        or fraud_outcome
    )

    if human["outcome"]:
        route = "HRBP_REVIEW"
    elif explicit_route:
        route = explicit_route
    elif description_action == "reject":
        route = "REJECT_SEMANTIC"
    elif (
        row.get("HrbpFlagId") is not None
        or row.get("NominationStatus") == "PendingHRBPReview"
        or (
            row.get("FdrNominationId") is not None
            and (fraud_concern or description_concern)
        )
    ):
        route = "HRBP_REVIEW"
    elif row.get("RejectionActor") == "Fraud Detection (Description)":
        route = "REJECT_SEMANTIC"
    else:
        route = "MANAGER_APPROVAL"

    if route != "HRBP_REVIEW":
        rule = (
            "legacy_semantic_rejection_backfill"
            if route == "REJECT_SEMANTIC"
            else "legacy_manager_approval_backfill"
        )
        return route, rule, None

    explicit_scope = summary.get("review_scope")
    if explicit_scope in _ALLOWED_SCOPES:
        scope = explicit_scope
    elif (description_concern or semantic_outcome) and fraud_concern:
        scope = "FRAUD_AND_SEMANTIC"
    elif description_concern or semantic_outcome:
        scope = "SEMANTIC"
    else:
        scope = "FRAUD"

    # Enforce the v2 scope/outcome contract even when old FeatureSummaryJson
    # omitted or misclassified the review source.
    if fraud_outcome and scope == "SEMANTIC":
        scope = "FRAUD_AND_SEMANTIC" if description_concern else "FRAUD"
    if semantic_outcome and scope == "FRAUD":
        scope = "FRAUD_AND_SEMANTIC" if fraud_concern else "SEMANTIC"

    rule = _first(
        summary.get("routing_rule"),
        {
            "FRAUD": "legacy_fraud_concern_hrbp",
            "SEMANTIC": "legacy_semantic_concern_hrbp",
            "FRAUD_AND_SEMANTIC": "legacy_combined_concern_hrbp",
        }[scope],
    )
    return route, str(rule), scope


def _decisive_engines(
    row: dict,
    summary: dict,
    engines: dict[str, dict],
    route: str,
    scope: str | None,
    composite_risk: str,
) -> list[str]:
    explicit = _json_list(summary.get("decisive_engines"))
    if not explicit and row.get("DecisiveModels"):
        explicit = str(row["DecisiveModels"]).split(",")
    if not explicit:
        explicit = summary.get("decisive_models") or []
    normalized = []
    names = {"RF": "RF", "GRAPH": "GRAPH", "GNN": "GNN", "SEMANTIC": "SEMANTIC"}
    for item in explicit:
        name = names.get(str(item).strip().upper())
        if name and name not in normalized:
            normalized.append(name)

    if route == "REJECT_SEMANTIC" or scope == "SEMANTIC":
        return ["SEMANTIC"]
    if not normalized:
        normalized = [
            name.upper()
            for name, engine in engines.items()
            if name != "semantic"
            and engine["available"]
            and engine["risk_level"] == composite_risk
        ]
    if scope == "FRAUD_AND_SEMANTIC" and "SEMANTIC" not in normalized:
        normalized.append("SEMANTIC")
    return normalized


def _timestamp(values: list[Any], *, latest: bool) -> datetime:
    dates = [value for value in values if isinstance(value, datetime)]
    if dates:
        return max(dates) if latest else min(dates)
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _build_record(row: dict) -> dict:
    summary = _json_object(row.get("FeatureSummaryJson"))
    engines = {
        "rf": _rf_document(row, summary),
        "graph": _graph_document(row, summary),
        "gnn": _gnn_document(row, summary),
        "semantic": _semantic_document(summary),
    }
    composite_score, composite_risk = _composite(row, engines)
    human = _human_review(row)
    route, rule, scope = _route_and_scope(
        row, summary, composite_risk, human
    )
    decisive = _decisive_engines(
        row, summary, engines, route, scope, composite_risk
    )
    dates = [
        row.get("FdrCreatedAt"), row.get("FdrUpdatedAt"),
        row.get("P2PCreatedAt"), row.get("GraphCreatedAt"),
        row.get("GraphUpdatedAt"), row.get("GnnCreatedAt"),
        row.get("HrbpCreatedAt"), row.get("NominationUpdatedAt"),
    ]
    record = {
        "nomination_id": row["NominationId"],
        "decision_schema_version": 2,
        "policy_version": _first(row.get("PolicyVersion"), summary.get("policy_version"), "legacy-backfill-v1"),
        "source_message_id": None,
        "rf_json": json.dumps(engines["rf"], default=str, separators=(",", ":")),
        "graph_json": json.dumps(engines["graph"], default=str, separators=(",", ":")),
        "gnn_json": json.dumps(engines["gnn"], default=str, separators=(",", ":")),
        "semantic_json": json.dumps(engines["semantic"], default=str, separators=(",", ":")),
        "composite_score": composite_score,
        "composite_risk": composite_risk,
        "decisive_json": json.dumps(decisive, separators=(",", ":")),
        "final_route": route,
        "routing_rule": rule,
        "review_scope": scope,
        "human_outcome": human["outcome"],
        "training_disposition": human["training"],
        "review_reason": human["reason"],
        "reviewed_by": human["reviewed_by"],
        "reviewed_at": human["reviewed_at"],
        "scored_by": _MIGRATION_ACTOR,
        "created_at": _timestamp(dates, latest=False),
        "updated_at": _timestamp(dates, latest=True),
    }
    _validate_record(record)
    return record


def _validate_record(record: dict) -> None:
    """Fail before insertion when reconstructed data violates the v2 contract."""
    nomination_id = record["nomination_id"]
    for field in ("rf_json", "graph_json", "gnn_json", "semantic_json"):
        if not isinstance(json.loads(record[field]), dict):
            raise ValueError(f"Nomination {nomination_id}: {field} is not an object")
    if not isinstance(json.loads(record["decisive_json"]), list):
        raise ValueError(
            f"Nomination {nomination_id}: decisive_json is not an array"
        )
    if record["composite_risk"] not in _RISK_RANK:
        raise ValueError(
            f"Nomination {nomination_id}: invalid composite risk"
        )
    score = record["composite_score"]
    if score is not None and not 0 <= score <= 100:
        raise ValueError(
            f"Nomination {nomination_id}: composite score outside 0-100"
        )
    if record["final_route"] == "HRBP_REVIEW":
        if record["review_scope"] not in _ALLOWED_SCOPES:
            raise ValueError(
                f"Nomination {nomination_id}: HRBP route has no valid scope"
            )
    elif record["review_scope"] is not None:
        raise ValueError(
            f"Nomination {nomination_id}: non-HRBP route has a review scope"
        )
    if record["human_outcome"] is None:
        human_fields = (
            "training_disposition", "review_reason", "reviewed_by", "reviewed_at"
        )
        if any(record[field] is not None for field in human_fields):
            raise ValueError(
                f"Nomination {nomination_id}: incomplete null human outcome"
            )
    elif any(
        record[field] is None
        for field in (
            "training_disposition", "review_reason", "reviewed_by", "reviewed_at"
        )
    ):
        raise ValueError(
            f"Nomination {nomination_id}: incomplete human adjudication"
        )
    outcome = record["human_outcome"]
    scope = record["review_scope"]
    if outcome in {"CONFIRMED_CONCERN", "CLEARED_NO_CONCERN"} and scope not in {
        "FRAUD", "FRAUD_AND_SEMANTIC"
    }:
        raise ValueError(
            f"Nomination {nomination_id}: fraud outcome has semantic-only scope"
        )
    if outcome == "CONFIRMED_SEMANTIC_CONCERN" and scope not in {
        "SEMANTIC", "FRAUD_AND_SEMANTIC"
    }:
        raise ValueError(
            f"Nomination {nomination_id}: semantic outcome has fraud-only scope"
        )


_SOURCE_SQL = sa.text("""
    WITH LegacyNominationIds AS (
        SELECT NominationId FROM dbo.P2P_FraudScores
        UNION SELECT NominationId FROM dbo.GNN_FraudScores
        UNION SELECT NominationId FROM dbo.Graph_FraudScores
        UNION SELECT NominationId FROM dbo.HRBP_FraudFlags
        UNION SELECT NominationId FROM dbo.FraudDecisionResults
    )
    SELECT
        ids.NominationId,
        n.Status AS NominationStatus,
        n.RejectionActor,
        n.RejectionReason,
        n.updated_at AS NominationUpdatedAt,

        fdr.NominationId AS FdrNominationId,
        fdr.PolicyVersion,
        fdr.RfAvailable, fdr.RfScore, fdr.RfRiskLevel,
        fdr.RfUnavailableReasonCode, fdr.RfUnavailableReasonDetail,
        fdr.GraphAvailable, fdr.GraphScore AS GraphDecisionScore,
        fdr.GraphRiskLevel,
        fdr.GraphUnavailableReasonCode, fdr.GraphUnavailableReasonDetail,
        fdr.GnnAvailable, fdr.GnnScore AS GnnDecisionScore,
        fdr.GnnRiskLevel,
        fdr.GnnUnavailableReasonCode, fdr.GnnUnavailableReasonDetail,
        fdr.FinalScore, fdr.FinalRiskLevel, fdr.DecisiveModels,
        fdr.ScoredBy AS FdrScoredBy,
        fdr.HumanReviewOutcome, fdr.TrainingDisposition,
        fdr.ReviewReason, fdr.ReviewedBy, fdr.ReviewedAt,
        fdr.CreatedAt AS FdrCreatedAt, fdr.UpdatedAt AS FdrUpdatedAt,

        p2p.P2PScoreId, p2p.FraudScore AS P2PFraudScore,
        p2p.RiskLevel AS P2PRiskLevel, p2p.FraudFlags AS P2PFraudFlags,
        p2p.IsFraud AS P2PIsFraud, p2p.ConfirmedBy AS P2PConfirmedBy,
        p2p.ConfirmedAt AS P2PConfirmedAt, p2p.CreatedAt AS P2PCreatedAt,

        graph.GraphScoreId, graph.GraphScore AS ComponentGraphScore,
        graph.RiskLevel AS ComponentGraphRiskLevel, graph.GraphFlags,
        graph.SnapshotAsOfDate, graph.ScoredBy AS GraphScoredBy,
        graph.CreatedAt AS GraphCreatedAt, graph.UpdatedAt AS GraphUpdatedAt,

        gnn.GNNScoreId, gnn.FraudScore AS ComponentGnnScore,
        gnn.FraudProbability AS GnnFraudProbability,
        gnn.RiskLevel AS ComponentGnnRiskLevel,
        gnn.FraudFlags AS GnnFraudFlags, gnn.ModelVersion AS GnnModelVersion,
        gnn.EmbeddingAsOfDate, gnn.ScoredBy AS GnnScoredBy,
        gnn.CreatedAt AS GnnCreatedAt,

        hrbp.FlagId AS HrbpFlagId, hrbp.FraudScore AS HrbpFraudScore,
        hrbp.FraudProbability AS HrbpFraudProbability,
        hrbp.RiskLevel AS HrbpRiskLevel, hrbp.WarningFlags,
        hrbp.TopFeaturesJson, hrbp.FeatureSummaryJson,
        hrbp.CreatedAt AS HrbpCreatedAt
    FROM LegacyNominationIds ids
    JOIN dbo.Nominations n ON n.NominationId = ids.NominationId
    LEFT JOIN dbo.FraudDecisionResults fdr
        ON fdr.NominationId = ids.NominationId
    LEFT JOIN dbo.P2P_FraudScores p2p
        ON p2p.NominationId = ids.NominationId
    OUTER APPLY (
        SELECT TOP (1) item.*
        FROM dbo.Graph_FraudScores item
        WHERE item.NominationId = ids.NominationId
        ORDER BY item.SnapshotAsOfDate DESC, item.UpdatedAt DESC,
                 item.GraphScoreId DESC
    ) graph
    OUTER APPLY (
        SELECT TOP (1) item.*
        FROM dbo.GNN_FraudScores item
        WHERE item.NominationId = ids.NominationId
        ORDER BY item.CreatedAt DESC, item.GNNScoreId DESC
    ) gnn
    LEFT JOIN dbo.HRBP_FraudFlags hrbp
        ON hrbp.NominationId = ids.NominationId
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.IntegrityDecisionResults current_result
        WHERE current_result.NominationId = ids.NominationId
    );
""")


_INSERT_SQL = sa.text("""
    INSERT INTO dbo.IntegrityDecisionResults (
        NominationId, DecisionSchemaVersion, PolicyVersion, SourceMessageId,
        RfResultJson, GraphResultJson, GnnResultJson, SemanticResultJson,
        CompositeScore, CompositeRiskLevel, DecisiveEnginesJson,
        FinalRoute, RoutingRule, ReviewScope,
        HumanReviewOutcome, TrainingDisposition, ReviewReason,
        ReviewedBy, ReviewedAt, ScoredBy, CreatedAt, UpdatedAt
    )
    SELECT
        :nomination_id, :decision_schema_version, :policy_version,
        :source_message_id, :rf_json, :graph_json, :gnn_json, :semantic_json,
        :composite_score, :composite_risk, :decisive_json,
        :final_route, :routing_rule, :review_scope,
        :human_outcome, :training_disposition, :review_reason,
        :reviewed_by, :reviewed_at, :scored_by, :created_at, :updated_at
    WHERE NOT EXISTS (
        SELECT 1 FROM dbo.IntegrityDecisionResults WITH (UPDLOCK, HOLDLOCK)
        WHERE NominationId = :nomination_id
    );
""")


def upgrade() -> None:
    missing = [name for name in _REQUIRED_TABLES if not _table_exists(name)]
    if missing:
        raise RuntimeError(
            "0048 requires the complete legacy and v2 schemas; missing: "
            + ", ".join(missing)
        )

    connection = op.get_bind()
    rows = [dict(row) for row in connection.execute(_SOURCE_SQL).mappings().all()]
    records = [_build_record(row) for row in rows]
    logger.info("0048 reconstructing %d legacy integrity decisions", len(records))
    if records:
        connection.execute(_INSERT_SQL, records)

    remaining = connection.execute(sa.text("""
        WITH LegacyNominationIds AS (
            SELECT NominationId FROM dbo.P2P_FraudScores
            UNION SELECT NominationId FROM dbo.GNN_FraudScores
            UNION SELECT NominationId FROM dbo.Graph_FraudScores
            UNION SELECT NominationId FROM dbo.HRBP_FraudFlags
            UNION SELECT NominationId FROM dbo.FraudDecisionResults
        )
        SELECT COUNT(*)
        FROM LegacyNominationIds legacy
        JOIN dbo.Nominations n ON n.NominationId = legacy.NominationId
        WHERE NOT EXISTS (
            SELECT 1 FROM dbo.IntegrityDecisionResults result
            WHERE result.NominationId = legacy.NominationId
        );
    """)).scalar_one()
    if remaining:
        raise RuntimeError(
            f"0048 validation failed: {remaining} legacy nominations remain unbackfilled"
        )
    logger.info("0048 backfill complete; no legacy nominations remain unmapped")


def downgrade() -> None:
    if _table_exists("IntegrityDecisionResults"):
        op.execute(
            "DELETE FROM dbo.IntegrityDecisionResults "
            f"WHERE ScoredBy = '{_MIGRATION_ACTOR}';"
        )
