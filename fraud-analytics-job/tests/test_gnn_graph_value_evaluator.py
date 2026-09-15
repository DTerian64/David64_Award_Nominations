"""Diagnostic-only graph value evaluator contract tests.

Run: python -m pytest tests/test_gnn_graph_value_evaluator.py -v
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modeling.gnn import graph as G
from modeling.gnn.evaluators.graph_value_by_ablation import evaluate_graph_value
from modeling.gnn.evaluators.graph_value_by_ablation.feature_profiles import (
    MLP_TABULAR,
    apply_feature_profile,
)
from modeling.gnn.evaluators.graph_value_by_ablation.scenarios import (
    evaluate_scenarios,
)
from tests.synthetic import make_tenant


def _fixture():
    users, nominations, labels = make_tenant(
        1, n_users=30, nominations_per_user=4, n_decoys=8
    )
    label_map = {
        row["NominationId"]: label
        for row, label in zip(nominations, labels)
    }
    folds = G.build_rolling_folds(users, nominations, n_folds=3)
    y_train = [
        np.asarray([label_map[nid] for nid in fold["train"]["nom_ids"]])
        for fold in folds
    ]
    y_eval = [
        np.asarray([label_map[nid] for nid in fold["eval"]["nom_ids"]])
        for fold in folds
    ]
    scenarios = {
        row["NominationId"]: ("RING" if label else "LEGITIMATE")
        for row, label in zip(nominations, labels)
    }
    return folds, y_train, y_eval, scenarios


def test_tabular_profile_removes_causal_context_without_mutating_source():
    folds, _, _, _ = _fixture()
    source = folds[-1]

    profiled = apply_feature_profile(source, MLP_TABULAR)

    assert profiled["train"]["x"].shape[1] == len(
        G.BASE_NOMINATION_FEATURE_COLUMNS
    )
    assert source["train"]["x"].shape[1] == len(G.NOMINATION_FEATURE_COLUMNS)


def test_scenario_metrics_use_explicit_metadata_and_legitimate_comparison():
    result = evaluate_scenarios(
        [
            {"candidate": "gatv2", "nomination_id": 1, "label": 1, "probability": 0.9},
            {"candidate": "gatv2", "nomination_id": 2, "label": 0, "probability": 0.1},
        ],
        {1: "RING", 2: "LEGITIMATE"},
    )

    ring = result["candidates"]["gatv2"]["scenarios"]["RING"]
    assert result["status"] == "COMPLETED"
    assert ring["fraud_example_count"] == 1
    assert ring["legitimate_comparison_count"] == 1
    assert ring["pr_auc"] == 1.0


def test_graph_value_evaluator_is_diagnostic_and_reports_both_ablation_steps():
    folds, y_train, y_eval, scenarios = _fixture()

    result = evaluate_graph_value(
        folds=folds,
        y_train_by_fold=y_train,
        y_eval_by_fold=y_eval,
        graph_architectures=["graphsage"],
        scenario_by_nomination_id=scenarios,
        hidden_dim=8,
        emb_dim=8,
        epochs=1,
    )

    assert result["role"] == "DIAGNOSTIC_ONLY"
    assert result["affects_serving_selection"] is False
    assert set(result["candidates"]) == {
        "mlp_tabular", "mlp_causal", "graphsage"
    }
    assert "engineered_causal_feature_gain" in result["ablation"]
    assert "graph_message_passing_gain" in result["ablation"]
