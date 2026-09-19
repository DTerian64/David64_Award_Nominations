"""Stable policy and result vocabulary for GNN v3 specialists."""

from __future__ import annotations

from dataclasses import dataclass


SERVING_MODE_V2 = "single_winner_v2"
SERVING_MODE_SPECIALISTS = "scenario_specialists"
SERVING_MODES = (
    SERVING_MODE_V2,
    SERVING_MODE_SPECIALISTS,
)

BEHAVIOR_TRACKS = (
    "RECIPROCAL",
    "RING",
    "TEMPORAL_BURST",
    "SUPER_NOMINATOR",
    "SUPER_BENEFICIARY",
    "BIPARTITE_DENSE_BLOCK",
)


@dataclass(frozen=True)
class SpecialistTrackPolicy:
    key: str
    enabled: bool
    candidate_architectures: tuple[str, ...]
    feature_contract: str
    minimum_train_positives: int
    minimum_train_negatives: int
    minimum_holdout_positives: int
    minimum_holdout_negatives: int
    minimum_evaluable_temporal_folds: int
    maximum_fold_pr_auc_range: float
    minimum_improvement_over_mlp: float
    maximum_holdout_brier_score: float
    maximum_holdout_inference_ms: float
    incumbent_refresh_tolerance: float
    architecture_switch_tolerance: float

    def snapshot(self) -> dict:
        return {
            **self.__dict__,
            "candidate_architectures": list(self.candidate_architectures),
        }
