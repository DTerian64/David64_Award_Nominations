"""Shared, model-independent primitives used by the GNN engine."""

from .causal_context import (
    CAUSAL_CONTEXT_FEATURE_COLUMNS,
    CAUSAL_FEATURE_SCHEMA_VERSION,
    causal_context_matrix,
    causal_context_values,
)

__all__ = [
    "CAUSAL_CONTEXT_FEATURE_COLUMNS",
    "CAUSAL_FEATURE_SCHEMA_VERSION",
    "causal_context_matrix",
    "causal_context_values",
]
