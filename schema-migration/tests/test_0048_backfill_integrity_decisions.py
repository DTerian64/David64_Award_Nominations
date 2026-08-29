"""Tests for migration 0048's deterministic legacy-to-v2 mapping.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\schema-migration"
    python -m pytest tests/test_0048_backfill_integrity_decisions.py -v
"""

import importlib.util
import json
from datetime import datetime
from pathlib import Path


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0048_backfill_integrity_decision_results.py"
)
_SPEC = importlib.util.spec_from_file_location("migration_0048", _MIGRATION_PATH)
migration = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(migration)


def _base_row(**overrides):
    now = datetime(2026, 8, 28, 20, 0, 0)
    row = {
        "NominationId": 13881,
        "NominationStatus": "Pending",
        "RejectionActor": None,
        "RejectionReason": None,
        "NominationUpdatedAt": now,
        "FdrNominationId": None,
        "PolicyVersion": None,
        "RfAvailable": None,
        "RfScore": None,
        "RfRiskLevel": None,
        "RfUnavailableReasonCode": None,
        "RfUnavailableReasonDetail": None,
        "GraphAvailable": None,
        "GraphDecisionScore": None,
        "GraphRiskLevel": None,
        "GraphUnavailableReasonCode": None,
        "GraphUnavailableReasonDetail": None,
        "GnnAvailable": None,
        "GnnDecisionScore": None,
        "GnnRiskLevel": None,
        "GnnUnavailableReasonCode": None,
        "GnnUnavailableReasonDetail": None,
        "FinalScore": None,
        "FinalRiskLevel": None,
        "DecisiveModels": None,
        "FdrScoredBy": None,
        "HumanReviewOutcome": None,
        "TrainingDisposition": None,
        "ReviewReason": None,
        "ReviewedBy": None,
        "ReviewedAt": None,
        "FdrCreatedAt": None,
        "FdrUpdatedAt": None,
        "P2PScoreId": None,
        "P2PFraudScore": None,
        "P2PRiskLevel": None,
        "P2PFraudFlags": None,
        "P2PIsFraud": None,
        "P2PConfirmedBy": None,
        "P2PConfirmedAt": None,
        "P2PCreatedAt": None,
        "GraphScoreId": None,
        "ComponentGraphScore": None,
        "ComponentGraphRiskLevel": None,
        "GraphFlags": None,
        "SnapshotAsOfDate": None,
        "GraphScoredBy": None,
        "GraphCreatedAt": None,
        "GraphUpdatedAt": None,
        "GNNScoreId": None,
        "ComponentGnnScore": None,
        "GnnFraudProbability": None,
        "ComponentGnnRiskLevel": None,
        "GnnFraudFlags": None,
        "GnnModelVersion": None,
        "EmbeddingAsOfDate": None,
        "GnnScoredBy": None,
        "GnnCreatedAt": None,
        "HrbpFlagId": None,
        "HrbpFraudScore": None,
        "HrbpFraudProbability": None,
        "HrbpRiskLevel": None,
        "WarningFlags": None,
        "TopFeaturesJson": None,
        "FeatureSummaryJson": None,
        "HrbpCreatedAt": None,
    }
    row.update(overrides)
    return row


def _document(record, name):
    return json.loads(record[f"{name}_json"])


def test_fdr_is_authoritative_and_hrbp_enriches_four_engine_evidence():
    summary = {
        "policy_version": "max-severity-v1",
        "description_action": "flag",
        "description_check": "category_alignment",
        "description_reason": "Category fit requires review.",
        "rf": {
            "fraud_score": 9,
            "fraud_prob": 0.07,
            "risk_level": "NONE",
            "llm_explanation": "RF evidence was reviewed.",
        },
    }
    record = migration._build_record(_base_row(
        FdrNominationId=13881,
        PolicyVersion="max-severity-v1",
        RfAvailable=1,
        RfScore=7,
        RfRiskLevel="NONE",
        GraphAvailable=1,
        GraphDecisionScore=75,
        GraphRiskLevel="HIGH",
        GnnAvailable=0,
        GnnDecisionScore=0,
        GnnRiskLevel="NONE",
        GnnUnavailableReasonCode="BELOW_MINIMUM_VOLUME",
        GnnUnavailableReasonDetail="90 nominations; requires 300",
        FinalScore=75,
        FinalRiskLevel="HIGH",
        DecisiveModels="Graph",
        FdrCreatedAt=datetime(2026, 8, 28, 19, 59),
        FdrUpdatedAt=datetime(2026, 8, 28, 20, 1),
        P2PScoreId=10,
        P2PFraudScore=9,
        P2PRiskLevel="NONE",
        P2PFraudFlags="[RF] Reciprocal nomination detected",
        P2PCreatedAt=datetime(2026, 8, 28, 19, 58),
        GraphScoreId=11,
        ComponentGraphScore=50,
        ComponentGraphRiskLevel="MEDIUM",
        GraphFlags="[Graph] Beneficiary is a super-nominator outlier",
        SnapshotAsOfDate=datetime(2026, 8, 27),
        GNNScoreId=12,
        ComponentGnnScore=61,
        GnnFraudProbability=0.61,
        ComponentGnnRiskLevel="HIGH",
        GnnModelVersion="gnn-v1",
        HrbpFlagId=13,
        HrbpFraudScore=75,
        HrbpFraudProbability=0.75,
        HrbpRiskLevel="HIGH",
        TopFeaturesJson='[{"feature":"pair_count"}]',
        FeatureSummaryJson=json.dumps(summary),
    ))

    assert record["composite_score"] == 75
    assert record["composite_risk"] == "HIGH"
    assert record["final_route"] == "HRBP_REVIEW"
    assert record["review_scope"] == "FRAUD_AND_SEMANTIC"
    assert json.loads(record["decisive_json"]) == ["GRAPH", "SEMANTIC"]
    assert _document(record, "rf")["score"] == 7
    assert _document(record, "rf")["model_probability"] == 0.07
    assert _document(record, "rf")["explanation"]["top_features"] == [
        {"feature": "pair_count"}
    ]
    assert _document(record, "graph")["score"] == 75
    assert _document(record, "gnn")["available"] is False
    assert _document(record, "gnn")["score"] is None
    assert _document(record, "semantic")["combined_decision"]["action"] == "flag"


def test_semantic_only_hrbp_route_does_not_name_clean_models_as_decisive():
    record = migration._build_record(_base_row(
        FdrNominationId=13882,
        RfAvailable=1,
        RfScore=7,
        RfRiskLevel="NONE",
        GraphAvailable=1,
        GraphDecisionScore=0,
        GraphRiskLevel="NONE",
        GnnAvailable=0,
        GnnRiskLevel="NONE",
        FinalScore=7,
        FinalRiskLevel="NONE",
        DecisiveModels="RF,Graph",
        HrbpFlagId=20,
        HrbpFraudScore=7,
        HrbpFraudProbability=0.07,
        HrbpRiskLevel="NONE",
        FeatureSummaryJson=json.dumps({
            "description_action": "flag",
            "description_check": "category_alignment",
            "description_reason": "Category concern.",
        }),
    ))

    assert record["review_scope"] == "SEMANTIC"
    assert json.loads(record["decisive_json"]) == ["SEMANTIC"]


def test_no_available_legacy_decision_maps_zero_to_null_composite_score():
    record = migration._build_record(_base_row(
        FdrNominationId=13883,
        PolicyVersion="max-severity-v1",
        RfAvailable=0,
        RfScore=0,
        RfRiskLevel="NONE",
        GraphAvailable=0,
        GraphDecisionScore=0,
        GraphRiskLevel="NONE",
        GnnAvailable=0,
        GnnDecisionScore=0,
        GnnRiskLevel="NONE",
        FinalScore=0,
        FinalRiskLevel="UNKNOWN",
    ))

    assert record["composite_score"] is None
    assert record["composite_risk"] == "UNKNOWN"
    assert record["final_route"] == "MANAGER_APPROVAL"


def test_incomplete_legacy_processing_recovers_the_intended_route_from_evidence():
    fraud_record = migration._build_record(_base_row(
        NominationStatus="Submitted",
        FdrNominationId=13884,
        RfAvailable=1,
        RfScore=68,
        RfRiskLevel="HIGH",
        GraphAvailable=0,
        GnnAvailable=0,
        FinalScore=68,
        FinalRiskLevel="HIGH",
        DecisiveModels="RF",
    ))
    semantic_record = migration._build_record(_base_row(
        NominationId=13885,
        NominationStatus="Submitted",
        FdrNominationId=13885,
        RfAvailable=1,
        RfScore=7,
        RfRiskLevel="NONE",
        GraphAvailable=0,
        GnnAvailable=0,
        FinalScore=7,
        FinalRiskLevel="NONE",
        FeatureSummaryJson=json.dumps({
            "description_action": "reject",
            "description_check": "category_alignment",
            "description_reason": "Description was incoherent.",
        }),
    ))

    assert fraud_record["final_route"] == "HRBP_REVIEW"
    assert fraud_record["review_scope"] == "FRAUD"
    assert semantic_record["final_route"] == "REJECT_SEMANTIC"
    assert semantic_record["review_scope"] is None
    assert json.loads(semantic_record["decisive_json"]) == ["SEMANTIC"]


def test_legacy_p2p_human_confirmation_becomes_model_neutral_adjudication():
    confirmed_at = datetime(2026, 8, 28, 20, 5)
    record = migration._build_record(_base_row(
        NominationStatus="Rejected",
        RejectionReason="Reciprocal activity confirmed.",
        P2PScoreId=30,
        P2PFraudScore=91,
        P2PRiskLevel="CRITICAL",
        P2PIsFraud=1,
        P2PConfirmedBy="HRBP:77",
        P2PConfirmedAt=confirmed_at,
        P2PCreatedAt=datetime(2026, 8, 28, 20, 0),
    ))

    assert record["final_route"] == "HRBP_REVIEW"
    assert record["review_scope"] == "FRAUD"
    assert record["human_outcome"] == "CONFIRMED_CONCERN"
    assert record["training_disposition"] == "FRAUD"
    assert record["reviewed_by"] == "HRBP:77"
    assert record["reviewed_at"] == confirmed_at


def test_hrbp_only_legacy_row_recovers_the_original_rf_snapshot():
    record = migration._build_record(_base_row(
        HrbpFlagId=40,
        HrbpFraudScore=64,
        HrbpFraudProbability=0.64321,
        HrbpRiskLevel="HIGH",
        WarningFlags="Reciprocal nomination detected, Unusually high amount",
        TopFeaturesJson='[{"feature":"amount_zscore"}]',
        HrbpCreatedAt=datetime(2026, 8, 28, 19, 0),
    ))

    rf = _document(record, "rf")
    assert rf["available"] is True
    assert rf["score"] == 64
    assert rf["model_probability"] == 0.64321
    assert rf["probability_source"] == "HRBP_FraudFlags"
    assert record["final_route"] == "HRBP_REVIEW"
    assert record["review_scope"] == "FRAUD"


def test_source_query_uses_latest_graph_and_gnn_rows_and_skips_live_v2_rows():
    sql = str(migration._SOURCE_SQL)

    assert "SnapshotAsOfDate DESC" in sql
    assert "GNNScoreId DESC" in sql
    assert "NOT EXISTS" in sql
    assert "IntegrityDecisionResults current_result" in sql
