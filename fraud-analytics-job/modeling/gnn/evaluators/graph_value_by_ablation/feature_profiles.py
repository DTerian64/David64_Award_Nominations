"""Feature profiles used by the graph-value ablation."""

from __future__ import annotations

from ...graph import BASE_NOMINATION_FEATURE_COLUMNS


MLP_TABULAR = "mlp_tabular"
MLP_CAUSAL = "mlp_causal"
FULL_CAUSAL = "full_causal"


def apply_feature_profile(graph: dict, profile: str) -> dict:
    """Return a shallow graph view with the requested nomination features.

    Message-passing data and target identities are shared. Only the target
    feature matrices are sliced; the source fold is never mutated.
    """
    if profile in {MLP_CAUSAL, FULL_CAUSAL}:
        return graph
    if profile != MLP_TABULAR:
        raise ValueError(f"Unknown GNN diagnostic feature profile: {profile}")

    columns = list(graph["nomination_feature_columns"])
    indices = [columns.index(name) for name in BASE_NOMINATION_FEATURE_COLUMNS]
    profiled = dict(graph)
    for split in ("train", "eval"):
        target = dict(graph[split])
        target["x"] = graph[split]["x"][:, indices]
        profiled[split] = target
    profiled["nomination_feature_columns"] = list(BASE_NOMINATION_FEATURE_COLUMNS)
    return profiled
