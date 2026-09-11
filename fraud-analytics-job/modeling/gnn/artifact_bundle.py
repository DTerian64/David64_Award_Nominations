"""Immutable, weights-only-safe artifacts for GNN v2 serving and explanation."""

from __future__ import annotations

import io
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import torch


SNAPSHOT_SCHEMA_VERSION = 1


def _iso(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def build_snapshot(
    *,
    graph: dict,
    tenant_id: int,
    model_version: str,
    graph_snapshot_id: str,
) -> dict:
    """Return a primitive/tensor-only snapshot accepted by weights_only=True."""
    data = graph["data"]
    user_ids = sorted(graph["user_index"], key=graph["user_index"].get)
    category_ids = sorted(
        graph.get("category_index", {}), key=graph.get("category_index", {}).get
    )
    edge_records = []
    for source, relation, target in sorted(data.edge_types):
        edge_records.append({
            "source": source,
            "relationship": relation,
            "target": target,
            "edge_index": data[source, relation, target].edge_index.detach().cpu(),
        })

    return {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "feature_schema_version": graph["feature_schema_version"],
        "tenant_id": int(tenant_id),
        "model_version": str(model_version),
        "graph_snapshot_id": str(graph_snapshot_id),
        "graph_snapshot_as_of": _iso(
            graph.get("graph_snapshot_as_of", graph["t_graph"])
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "node_features": {
            node_type: data[node_type].x.detach().cpu()
            for node_type in sorted(data.node_types)
        },
        "edges": edge_records,
        "mappings": {
            "user_ids": [int(value) for value in user_ids],
            "nomination_ids": [
                int(value) for value in graph["graph_nomination_ids"]
            ],
            "category_ids": [int(value) for value in category_ids],
        },
        "user_feature_columns": list(graph["user_feature_columns"]),
        "nomination_feature_columns": list(graph["nomination_feature_columns"]),
        "user_scaler_mean": [
            float(value) for value in graph["user_scaler"]["mean"]
        ],
        "user_scaler_std": [
            float(value) for value in graph["user_scaler"]["std"]
        ],
        "nomination_scaler_mean": [
            float(value) for value in graph["nomination_scaler"]["mean"]
        ],
        "nomination_scaler_std": [
            float(value) for value in graph["nomination_scaler"]["std"]
        ],
        "category_amount_stats": graph["category_amount_stats"],
    }


def write_snapshot(path: Path, snapshot: dict) -> None:
    """Write and immediately prove safe restricted deserialization."""
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(snapshot, path)
    with path.open("rb") as handle:
        restored = torch.load(
            io.BytesIO(handle.read()), map_location="cpu", weights_only=True
        )
    if restored.get("graph_snapshot_id") != snapshot.get("graph_snapshot_id"):
        raise ValueError("GNN graph snapshot failed its identity round trip")


def snapshot_to_hetero_inputs(snapshot: dict) -> tuple[dict, dict]:
    """Reconstruct x_dict and edge_index_dict without importing HeteroData."""
    x_dict = {
        key: value for key, value in snapshot["node_features"].items()
    }
    edge_index_dict = {
        (row["source"], row["relationship"], row["target"]): row["edge_index"]
        for row in snapshot["edges"]
    }
    return x_dict, edge_index_dict
