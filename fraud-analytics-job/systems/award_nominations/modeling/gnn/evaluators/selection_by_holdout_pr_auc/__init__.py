"""Operational GNN selection by untouched-holdout PR-AUC."""

from .evaluator import select_architecture
from .policy import GRAPH_ARCHITECTURES, SELECTION_METRIC, SELECTION_POLICY_VERSION

__all__ = [
    "GRAPH_ARCHITECTURES",
    "SELECTION_METRIC",
    "SELECTION_POLICY_VERSION",
    "select_architecture",
]

