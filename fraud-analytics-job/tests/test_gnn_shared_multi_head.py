"""V4 shared-encoder and model-neutral multi-label supervision contracts."""

import numpy as np
import pandas as pd
import torch
from types import SimpleNamespace

from modeling.gnn.evaluators.selection_by_temporal_validation.model import (
    PatternTargets,
    build_pattern_targets,
    masked_joint_loss,
    MultiHeadDecoder,
)
from modeling.gnn.specialists.contracts import BEHAVIOR_TRACKS
from modeling.gnn.specialists.feature_contracts import FEATURE_CONTRACTS
from modeling.gnn.graph import NOMINATION_FEATURE_COLUMNS
from modeling.gnn.evaluators.selection_by_temporal_validation.evaluator import (
    _fit, evaluate_shared_model, select_shared_architecture,
)
from modeling.gnn.evaluators.metrics import display_threshold_metrics
from modeling.gnn import graph as G
from tests.synthetic import make_tenant


def test_synthetic_patterns_are_complete_and_can_be_multi_label():
    labels = pd.DataFrame([
        {"NominationId": 1, "IsFraud": 1, "LabelSource": "synthetic_ground_truth",
         "ConfirmedPatterns": ("RING", "RECIPROCAL")},
        {"NominationId": 2, "IsFraud": 0, "LabelSource": "synthetic_ground_truth",
         "ConfirmedPatterns": ()},
    ])
    result = build_pattern_targets(labels, [1, 2])
    assert result.overall.tolist() == [1, 0]
    assert result.patterns[0, BEHAVIOR_TRACKS.index("RING")] == 1
    assert result.patterns[0, BEHAVIOR_TRACKS.index("RECIPROCAL")] == 1
    assert np.all(result.mask == 1)


def test_unadjudicated_human_patterns_are_masked_not_negative():
    labels = pd.DataFrame([
        {"NominationId": 3, "IsFraud": 1, "LabelSource": "hrbp",
         "ConfirmedPatterns": ("RING",)},
    ])
    result = build_pattern_targets(labels, [3])
    assert result.mask[0, BEHAVIOR_TRACKS.index("RING")] == 1
    assert result.mask[0, BEHAVIOR_TRACKS.index("RECIPROCAL")] == 0


def test_masked_loss_ignores_unknown_pattern_logits():
    targets = PatternTargets(
        np.array([1, 0], dtype=np.float32),
        np.array([[1, 0], [0, 0]], dtype=np.float32),
        np.array([[1, 0], [1, 0]], dtype=np.float32),
    )
    first = {"OVERALL": torch.tensor([0.1, -0.2]),
             "RECIPROCAL": torch.tensor([0.3, -0.4]),
             "RING": torch.tensor([100.0, -100.0])}
    second = {**first, "RING": -first["RING"]}
    assert torch.allclose(masked_joint_loss(first, targets), masked_joint_loss(second, targets))


def test_decoder_has_one_overall_and_independent_pattern_outputs():
    contracts = {"RING": "ring-v1", "RECIPROCAL": "reciprocal-v1"}
    decoder = MultiHeadDecoder(4, list(NOMINATION_FEATURE_COLUMNS), contracts)
    result = decoder(torch.randn(2, 4), torch.randn(2, 4),
                     torch.randn(2, len(NOMINATION_FEATURE_COLUMNS)))
    assert set(result) == {"OVERALL", "RING", "RECIPROCAL"}
    assert all(value.shape == (2,) for value in result.values())
    assert decoder.indices["RING"] == [
        list(NOMINATION_FEATURE_COLUMNS).index(name)
        for name in FEATURE_CONTRACTS["ring-v1"]
    ]


def test_architecture_selection_uses_overall_then_pattern_tie_breaker():
    candidates = {
        "graphsage": {"status": "COMPLETED", "validation_overall_pr_auc": 0.70,
                      "validation_macro_pattern_pr_auc": 0.5, "validation_inference_ms": 10},
        "gatv2": {"status": "COMPLETED", "validation_overall_pr_auc": 0.695,
                  "validation_macro_pattern_pr_auc": 0.6, "validation_inference_ms": 20},
        "gcn": {"status": "COMPLETED", "validation_overall_pr_auc": 0.60,
                "validation_macro_pattern_pr_auc": 0.99, "validation_inference_ms": 1},
    }
    assert select_shared_architecture(candidates, 0.01)["selected_architecture"] == "gatv2"
    assert select_shared_architecture(candidates, 0.001)["selected_architecture"] == "graphsage"


def test_display_threshold_diagnostics_match_inference_score_rounding():
    probabilities = np.array([0.446, 0.444, 0.46, 0.10])
    logits = np.log(probabilities / (1 - probabilities))
    result = display_threshold_metrics(np.array([1, 1, 0, 0]), logits, 45.0)

    assert result["alert_count"] == 2
    assert result["true_positive_count"] == 1
    assert result["false_positive_count"] == 1
    assert result["false_negative_count"] == 1
    assert result["true_negative_count"] == 1
    assert result["precision"] == 0.5
    assert result["recall"] == 0.5
    assert result["false_positive_rate"] == 0.5
    assert result["alert_rate"] == 0.5


def test_display_threshold_diagnostics_report_undefined_precision_without_alerts():
    result = display_threshold_metrics(
        np.array([1, 0]), np.array([-10.0, -10.0]), 45.0
    )
    assert result["alert_count"] == 0
    assert result["precision"] is None
    assert result["recall"] == 0.0


def test_joint_training_uses_one_graph_encoder_and_all_heads():
    users, nominations, labels = make_tenant(
        1, n_users=24, nominations_per_user=4, n_decoys=6,
    )
    folds = G.build_rolling_folds(users, nominations, n_folds=3)
    frame = pd.DataFrame([
        {"NominationId": nomination["NominationId"], "IsFraud": int(fraud),
         "LabelSource": "synthetic_ground_truth",
         "ConfirmedPatterns": ("RING", "RECIPROCAL") if fraud else ()}
        for nomination, fraud in zip(nominations, labels)
    ])
    model, metrics = _fit(
        folds[:1], frame, "graphsage",
        {"RING": "ring-v1", "RECIPROCAL": "reciprocal-v1"},
        hidden_dim=8, emb_dim=8, epochs=1,
        overall_weight=1.0, pattern_weight=1.0,
    )
    output = model(
        folds[0]["data"], folds[0]["train"]["pairs"], folds[0]["train"]["x"]
    )
    assert model.encoder is not None
    assert set(output) == {"OVERALL", "RING", "RECIPROCAL"}
    assert metrics["training_count"] == len(folds[0]["train"]["nom_ids"])


def test_v4_evaluation_keeps_final_period_out_of_architecture_selection():
    users, nominations, labels = make_tenant(
        1, n_users=30, nominations_per_user=5, n_decoys=6,
    )
    folds = G.build_rolling_folds(users, nominations, n_folds=3)
    frame = pd.DataFrame([
        {"NominationId": nomination["NominationId"], "IsFraud": int(fraud),
         "LabelSource": "synthetic_ground_truth",
         "ConfirmedPatterns": ("RING",) if fraud else ()}
        for nomination, fraud in zip(nominations, labels)
    ])
    policy = SimpleNamespace(
        pattern_heads=(("RING", "ring-v1", True),),
        hidden_dim=8, embed_dim=8, epochs=1,
        overall_loss_weight=1.0, pattern_total_loss_weight=1.0,
        candidate_architectures=("graphsage",), overall_tie_tolerance=0.01,
        minimum_graph_value_over_raw_mlp=0.0,
        minimum_message_passing_value_over_engineered_graph_mlp=0.0,
        maximum_overall_holdout_brier_score=1.0,
        maximum_holdout_inference_ms=100000,
        minimum_pattern_train_positives=1,
        minimum_pattern_validation_positives=1,
        minimum_pattern_holdout_positives=1,
        minimum_pattern_holdout_negatives=1,
        maximum_pattern_validation_pr_auc_range=1.0,
        minimum_pattern_improvement_over_engineered_mlp=0.0,
        maximum_pattern_holdout_brier_score=1.0,
        medium_threshold=45.0,
    )
    report, _model = evaluate_shared_model(folds, frame, policy)
    assert report["selection"]["selected_architecture"] == "graphsage"
    assert len(report["validation_candidates"]["graphsage"]["validation_folds"]) == 2
    assert report["final_test"]["selected_architecture"] == "graphsage"
    assert "display_threshold_diagnostics" in report["final_test"]["head_states"]["RING"]["final_test"]
