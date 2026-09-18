"""Database-free candidate models for the shared Tabular integrity engine."""

from .contracts import (
    TabularCandidateEvaluation,
    TabularCandidateResult,
    TabularSelectionPolicy,
    TabularSelectionResult,
    TabularTrainingPolicy,
    TemporalHoldout,
)
from .tabular_mlp import train_tabular_mlp_candidate
from .random_forest import train_random_forest_candidate
from .selection import evaluate_tabular_candidates

__all__ = [
    "TabularCandidateEvaluation",
    "TabularCandidateResult",
    "TabularSelectionPolicy",
    "TabularSelectionResult",
    "TabularTrainingPolicy",
    "TemporalHoldout",
    "evaluate_tabular_candidates",
    "train_tabular_mlp_candidate",
    "train_random_forest_candidate",
]
