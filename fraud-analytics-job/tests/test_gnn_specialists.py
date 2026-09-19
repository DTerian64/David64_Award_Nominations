"""Independent label and nested admission contracts for GNN specialists."""

import numpy as np
import pandas as pd

from modeling.gnn.specialists.contracts import SpecialistTrackPolicy
from modeling.gnn.specialists.evaluator import evaluate_specialist
from modeling.gnn.specialists.labels import build_specialist_label_maps


def test_other_fraud_patterns_are_excluded_not_negative():
    frame = pd.DataFrame([
        {"NominationId": 1, "IsFraud": 0, "ConfirmedPatterns": ()},
        {"NominationId": 2, "IsFraud": 1, "ConfirmedPatterns": ("RING",)},
        {
            "NominationId": 3,
            "IsFraud": 1,
            "ConfirmedPatterns": ("RING", "RECIPROCAL"),
        },
    ])

    labels = build_specialist_label_maps(frame)

    assert labels["RING"] == {1: 0, 2: 1, 3: 1}
    assert labels["RECIPROCAL"] == {1: 0, 3: 1}
    assert labels["TEMPORAL_BURST"] == {1: 0}


def _fold(index: int) -> dict:
    train_ids = [index * 10 + 1, index * 10 + 2]
    eval_ids = [index * 10 + 3, index * 10 + 4]
    columns = [
        "LogPriorDirectedPairCount",
        "LogPriorReversePairCount",
        "LogDirectedPairCount30d",
    ]
    return {
        "fold_index": index,
        "train": {
            "nom_ids": train_ids,
            "x": np.zeros((2, 3), dtype=np.float32),
            "pairs": np.zeros((2, 2), dtype=np.int64),
        },
        "eval": {
            "nom_ids": eval_ids,
            "x": np.zeros((2, 3), dtype=np.float32),
            "pairs": np.zeros((2, 2), dtype=np.int64),
        },
        "nomination_feature_columns": columns,
        "nomination_scaler": {
            "mean": np.zeros(3, dtype=np.float32),
            "std": np.ones(3, dtype=np.float32),
        },
    }


def test_nested_evaluator_selects_then_admits_on_final_holdout():
    folds = [_fold(index) for index in (1, 2, 3)]
    label_map = {}
    for fold in folds:
        for split in ("train", "eval"):
            ids = fold[split]["nom_ids"]
            label_map[ids[0]] = 1
            label_map[ids[1]] = 0

    def fake_evaluator(**kwargs):
        architecture = kwargs["architecture"]
        pr_auc = 0.50 if architecture == "mlp" else (
            0.75 if architecture == "gatv2" else 0.60
        )
        return ({
            "status": "COMPLETED",
            "architecture": architecture,
            "folds": [
                {
                    "pr_auc": pr_auc,
                    "brier_score": 0.10,
                    "inference_ms": 5.0,
                }
                for _ in kwargs["folds"]
            ],
        }, [])

    track = SpecialistTrackPolicy(
        key="RECIPROCAL",
        enabled=True,
        candidate_architectures=("graphsage", "gatv2"),
        feature_contract="reciprocal-v1",
        minimum_train_positives=1,
        minimum_train_negatives=1,
        minimum_holdout_positives=1,
        minimum_holdout_negatives=1,
        minimum_evaluable_temporal_folds=2,
        maximum_fold_pr_auc_range=0.2,
        minimum_improvement_over_mlp=0.02,
        maximum_holdout_brier_score=0.25,
        maximum_holdout_inference_ms=100.0,
        incumbent_refresh_tolerance=0.01,
        architecture_switch_tolerance=0.01,
    )

    result = evaluate_specialist(
        track=track,
        folds=folds,
        label_map=label_map,
        hidden_dim=8,
        emb_dim=8,
        epochs=1,
        candidate_evaluator=fake_evaluator,
    )

    assert result["status"] == "ADMITTED"
    assert result["provisional_architecture"] == "gatv2"
    assert result["improvement_over_mlp"] == 0.25
