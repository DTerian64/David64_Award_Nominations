"""Operational GNN candidate selection policy."""

from modeling.gnn.selection import select_architecture


def completed(value: float, **extra):
    return {"status": "COMPLETED", "eval_pr_auc": value, **extra}


def test_selects_best_graph_that_clears_mlp_admission_margin():
    result = select_architecture({
        "mlp": completed(0.60),
        "graphsage": completed(0.73),
        "gcn": completed(0.68),
        "gatv2": completed(0.70),
    })

    assert result["selected_architecture"] == "graphsage"
    assert result["selection_reason"] == "HIGHEST_ELIGIBLE_PR_AUC"
    assert round(result["improvement_over_mlp"], 2) == 0.13


def test_mlp_can_block_activation_but_cannot_be_selected():
    result = select_architecture({
        "mlp": completed(0.75),
        "graphsage": completed(0.74),
        "gcn": completed(0.72),
        "gatv2": completed(0.70),
    })

    assert result["selected_architecture"] is None
    assert result["selection_reason"] == "NO_GRAPH_VALUE_OVER_MLP"


def test_retains_incumbent_within_tolerance():
    result = select_architecture(
        {
            "mlp": completed(0.50),
            "graphsage": completed(0.70),
            "gcn": completed(0.705),
            "gatv2": completed(0.65),
        },
        incumbent_architecture="graphsage",
        incumbent_tie_tolerance=0.01,
    )

    assert result["selected_architecture"] == "graphsage"
    assert result["selection_reason"] == "INCUMBENT_RETAINED_WITHIN_TOLERANCE"


def test_requires_multiple_eligible_graph_candidates():
    result = select_architecture({
        "mlp": completed(0.50),
        "graphsage": completed(0.70),
        "gcn": {"status": "FAILED"},
        "gatv2": {"status": "FAILED"},
    })

    assert result["selected_architecture"] is None
    assert result["selection_reason"] == "INSUFFICIENT_ELIGIBLE_CANDIDATES"


def test_completed_candidate_can_fail_an_explicit_guardrail():
    result = select_architecture({
        "mlp": completed(0.50),
        "graphsage": completed(0.70, guardrail_failures=["ARTIFACT_INVALID"]),
        "gcn": completed(0.69, guardrail_failures=["ARTIFACT_INVALID"]),
        "gatv2": completed(0.68, guardrail_failures=["ARTIFACT_INVALID"]),
    })

    assert result["selected_architecture"] is None
    assert result["selection_reason"] == "CANDIDATE_GUARDRAIL_FAILED"
