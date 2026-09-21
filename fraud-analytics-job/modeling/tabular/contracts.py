"""Contracts for non-serving Tabular candidate training and evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class TabularTrainingPolicy:
    """Deterministic T3 policy; database persistence is a later phase."""

    evaluation_fraction: float = 0.20
    minimum_training_samples: int = 50
    minimum_evaluation_samples: int = 20
    minimum_class_samples_per_split: int = 2
    random_seed: int = 42
    permutation_importance_repeats: int = 10
    rf_estimators: int = 40
    rf_max_depth: int = 10
    rf_min_samples_split: int = 20
    rf_min_samples_leaf: int = 10
    mlp_hidden_layers: tuple[int, ...] = (64, 32)
    mlp_alpha: float = 0.0001
    mlp_batch_size: int = 256
    mlp_learning_rate: float = 0.001
    mlp_max_iterations: int = 200
    mlp_validation_fraction: float = 0.15
    mlp_patience: int = 15

    def __post_init__(self) -> None:
        if not 0 < self.evaluation_fraction < 1:
            raise ValueError("evaluation_fraction must be between zero and one")
        integer_fields = (
            "minimum_training_samples",
            "minimum_evaluation_samples",
            "minimum_class_samples_per_split",
            "permutation_importance_repeats",
            "rf_estimators",
            "rf_max_depth",
            "rf_min_samples_split",
            "rf_min_samples_leaf",
            "mlp_batch_size",
            "mlp_max_iterations",
            "mlp_patience",
        )
        for name in integer_fields:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not self.mlp_hidden_layers or any(
            dimension <= 0 for dimension in self.mlp_hidden_layers
        ):
            raise ValueError("mlp_hidden_layers must contain positive dimensions")
        if self.mlp_alpha < 0:
            raise ValueError("mlp_alpha cannot be negative")
        if self.mlp_learning_rate <= 0:
            raise ValueError("mlp_learning_rate must be positive")
        if not 0 < self.mlp_validation_fraction < 1:
            raise ValueError("mlp_validation_fraction must be between zero and one")


@dataclass(frozen=True, slots=True)
class TabularSelectionPolicy:
    selection_metric: str = "holdout_pr_auc"
    minimum_pr_auc_lift: float = 1.0
    tie_tolerance: float = 0.005
    preferred_architecture_on_tie: str = "random_forest"

    def __post_init__(self) -> None:
        if self.selection_metric != "holdout_pr_auc":
            raise ValueError("T4 supports holdout_pr_auc selection only")
        if self.minimum_pr_auc_lift < 1:
            raise ValueError("minimum_pr_auc_lift must be at least 1")
        if self.tie_tolerance < 0:
            raise ValueError("tie_tolerance cannot be negative")
        if self.preferred_architecture_on_tie not in {
            "random_forest",
            "tabular_mlp",
        }:
            raise ValueError("Unsupported tie-preference architecture")


@dataclass(frozen=True, slots=True)
class TemporalHoldout:
    train_index: tuple[Any, ...]
    evaluation_index: tuple[Any, ...]
    train_nomination_ids: tuple[int, ...]
    evaluation_nomination_ids: tuple[int, ...]
    train_end: str
    evaluation_start: str


@dataclass(slots=True)
class TabularCandidateResult:
    """In-memory candidate result; it cannot activate or publish itself."""

    architecture: str
    status: str
    feature_schema_id: str
    source_snapshot_id: str
    policy: TabularTrainingPolicy
    holdout: TemporalHoldout | None = None
    model: Any | None = None
    preprocessor: Any | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    evaluation_probabilities: tuple[float, ...] = ()
    evaluation_targets: tuple[int, ...] = ()
    reason_code: str | None = None
    reason_detail: str | None = None

    @property
    def completed(self) -> bool:
        return self.status == "COMPLETED"


@dataclass(frozen=True, slots=True)
class TabularSelectionResult:
    status: str
    selected_architecture: str | None
    selection_metric: str
    selection_reason: str
    candidate_evaluations: Mapping[str, Mapping[str, Any]]
    serving_state_changed: bool = False


@dataclass(frozen=True, slots=True)
class TabularCandidateEvaluation:
    candidates: Mapping[str, TabularCandidateResult]
    selection: TabularSelectionResult
