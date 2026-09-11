"""Tests for the versioned four-engine IntegrityDecisionResults JSON contract.

Usage (PowerShell):

    cd "C:\\Users\\David\\source\\repos\\David64_Award_Nominations\\Award_Nomination_App\\integrity-check"
    python -m pytest tests/test_decision_contract.py -v
"""

import json
import os


os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")

from inference import decision_contract
from inference.description_check import CheckResult


def test_unavailable_model_has_no_synthetic_score_or_probability():
    payload = decision_contract.gnn_result({
        "model_available": False,
        "fraud_score": 0,
        "fraud_prob": 0.0,
        "risk_level": "NONE",
        "unavailable_reason": "BELOW_MINIMUM_VOLUME",
        "unavailable_detail": "79 nominations; requires 300",
    })

    assert payload["available"] is False
    assert payload["score"] is None
    assert payload["model_probability"] is None
    assert payload["risk_level"] == "UNKNOWN"
    assert payload["unavailable_reason"] == "BELOW_MINIMUM_VOLUME"


def test_each_engine_preserves_its_own_kind_of_evidence():
    semantic = CheckResult(
        action="flag",
        reason="Category fit was weak.",
        check="category_alignment",
        evidence={
            "schema_version": 1,
            "engine": "SEMANTIC",
            "available": True,
            "status": "SUCCEEDED",
            "embedding": {"similarity": 0.11, "threshold": 0.12},
            "llm": {"response": {"category_fit_score": 0.35}},
            "duplicate_description": {"outcome": "pass"},
            "combined_decision": {"action": "flag"},
        },
    )
    payloads = decision_contract.build(
        {
            "model_available": True,
            "fraud_score": 48,
            "fraud_prob": 0.487,
            "risk_level": "MEDIUM",
            "warning_flags": ["Reciprocal nomination detected"],
            "shap_explanations": [{"feature": "pair_count"}],
        },
        {
            "model_available": True,
            "fraud_score": 75,
            "fraud_prob": None,
            "risk_level": "HIGH",
            "warning_flags": ["Nominator is a super-nominator outlier"],
            "winning_finding": {"finding_hash": "graph-1", "pattern_type": "Ring"},
            "candidate_detector_scores": [
                {"detector": "SuperBeneficiary", "score": 54.25,
                 "state": "NOT_SCORING", "eligible": False}
            ],
            "detector_summary": [{"pattern_type": "Ring", "scoring_count": 9}],
            "winning_pattern_type": "Ring",
            "winning_pattern_count": 9,
        },
        {
            "model_available": True,
            "fraud_score": 62,
            "fraud_prob": 0.618,
            "risk_level": "HIGH",
            "warning_flags": [],
            "model_version": "gnn-v2-test",
            "architecture": "gatv2",
            "training_policy_id": 12,
            "training_policy_version": 3,
            "scoring_policy_id": 14,
            "scoring_policy_version": 4,
            "graph_snapshot_id": "snapshot-test",
            "feature_schema_version": "gnn-v2",
            "explanation": {
                "method": "GNNEXPLAINER",
                "status": "REQUESTED",
                "request_id": "gnnexp:t1:n1:gnn-v2-test",
            },
        },
        semantic,
    )

    assert payloads["rf"]["model_probability"] == 0.487
    assert payloads["rf"]["explanation"]["top_features"] == [
        {"feature": "pair_count"}
    ]
    assert payloads["graph"]["model_probability"] is None
    assert payloads["graph"]["findings"] == [
        "Nominator is a super-nominator outlier"
    ]
    assert payloads["graph"]["winning_pattern_type"] == "Ring"
    assert payloads["graph"]["winning_pattern_count"] == 9
    assert payloads["graph"]["detector_summary"][0]["scoring_count"] == 9
    assert payloads["graph"]["candidate_detector_scores"] == [
        {"detector": "SuperBeneficiary", "score": 54.25,
         "state": "NOT_SCORING", "eligible": False}
    ]
    assert payloads["gnn"]["model_probability"] == 0.618
    assert payloads["gnn"]["schema_version"] == 2
    assert payloads["gnn"]["graph_snapshot_id"] == "snapshot-test"
    assert payloads["gnn"]["architecture"] == "gatv2"
    assert payloads["gnn"]["training_policy_version"] == 3
    assert payloads["gnn"]["scoring_policy_version"] == 4
    assert payloads["gnn"]["explanation"]["status"] == "REQUESTED"
    assert payloads["semantic"]["llm"]["response"]["category_fit_score"] == 0.35
    json.dumps(payloads)
