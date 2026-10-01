"""Versioned causal feature contracts shared by each track's candidates."""

from __future__ import annotations


FEATURE_CONTRACTS: dict[str, tuple[str, ...]] = {
    "reciprocal-v1": (
        "LogPriorDirectedPairCount",
        "LogPriorReversePairCount",
        "LogDirectedPairCount30d",
    ),
    "ring-v1": (
        "LogPriorDirectedPairCount",
        "LogPriorReversePairCount",
        "LogReverseTwoHopPathCount",
        "LogReverseThreeHopPathCount",
    ),
    "temporal-burst-v1": (
        "LogNominatorOutgoingCount30d",
        "LogNominatorUniqueBeneficiaries30d",
        "LogBeneficiaryIncomingCount30d",
        "LogBeneficiaryUniqueNominators30d",
        "LogBeneficiaryIncomingCount1h",
        "LogEndpointEdgeCount1h",
    ),
    "super-nominator-v1": (
        "LogNominatorOutgoingCount30d",
        "LogNominatorUniqueBeneficiaries30d",
        "LogDirectedPairCount30d",
    ),
    "super-beneficiary-v1": (
        "LogBeneficiaryIncomingCount30d",
        "LogBeneficiaryUniqueNominators30d",
        "LogBeneficiaryIncomingCount1h",
    ),
    "bipartite-dense-block-v1": (
        "LogNominatorOutgoingCount30d",
        "LogNominatorUniqueBeneficiaries30d",
        "LogBeneficiaryIncomingCount30d",
        "LogBeneficiaryUniqueNominators30d",
        "LogEndpointEdgeCount1h",
    ),
}


def apply_specialist_feature_contract(graph: dict, contract: str) -> dict:
    """Slice target features and their scaler; topology remains unchanged."""
    wanted = FEATURE_CONTRACTS.get(contract)
    if wanted is None:
        raise ValueError(f"Unknown specialist feature contract: {contract}")
    columns = list(graph["nomination_feature_columns"])
    try:
        indices = [columns.index(name) for name in wanted]
    except ValueError as exc:
        raise ValueError(
            f"Feature contract {contract} is incompatible with {columns}"
        ) from exc
    profiled = dict(graph)
    for split in ("train", "eval"):
        target = dict(graph[split])
        target["x"] = graph[split]["x"][:, indices]
        profiled[split] = target
    profiled["nomination_feature_columns"] = list(wanted)
    scaler = graph.get("nomination_scaler") or {}
    if "mean" in scaler and "std" in scaler:
        profiled["nomination_scaler"] = {
            "mean": scaler["mean"][indices],
            "std": scaler["std"][indices],
        }
    profiled["specialist_feature_contract"] = contract
    return profiled
