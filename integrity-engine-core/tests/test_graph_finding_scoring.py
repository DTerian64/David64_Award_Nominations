from integrity_engine.graph.finding_scoring import (
    calculate_graph_finding_score,
    derive_graph_finding_severity,
)


THRESHOLDS = {"low": 20, "medium": 40, "high": 60, "critical": 80}


def test_graph_finding_score_preserves_its_deterministic_derivation():
    score, derivation = calculate_graph_finding_score(
        base_score=30,
        minimum_score=0,
        maximum_score=100,
        parameters={"exposure_weight": 40, "repeat_weight": 20},
        signals={"exposure": 0.5, "repeat": 0.25},
    )

    assert score == 55
    assert derivation["finding_score"] == 55
    assert derivation["contributions"] == {"exposure": 20, "repeat": 5}


def test_graph_finding_severity_is_derived_from_graph_thresholds():
    assert derive_graph_finding_severity(79.99, THRESHOLDS) == "HIGH"
    assert derive_graph_finding_severity(80, THRESHOLDS) == "CRITICAL"
