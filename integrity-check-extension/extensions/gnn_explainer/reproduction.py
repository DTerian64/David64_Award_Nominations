"""Reproduce a persisted GNN score before any explanation is attempted."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone

import numpy as np
import torch

from .artifacts import ArtifactBundle
from .errors import PermanentExtensionError
from .model import build_decoder, build_encoder


@dataclass(frozen=True)
class ReproductionPolicy:
    probability_tolerance: float = 0.0005
    embedding_tolerance: float = 0.00001

    @classmethod
    def from_configuration(cls, configuration: dict | None) -> "ReproductionPolicy":
        explanation = (configuration or {}).get("explanation") or {}
        return cls(
            probability_tolerance=float(explanation.get("probability_reproduction_tolerance", 0.0005)),
            embedding_tolerance=float(explanation.get("embedding_reproduction_tolerance", 0.00001)),
        )


@dataclass(frozen=True)
class ReproductionResult:
    serving_probability: float
    reconstructed_probability: float
    stored_probability_difference: float
    graph_probability_difference: float
    embedding_max_difference: float


def _standardise(row: np.ndarray, mean, std) -> np.ndarray:
    mean_array = np.asarray(mean, dtype=np.float32)
    std_array = np.asarray(std, dtype=np.float32)
    std_array = np.where(std_array < 1e-8, 1.0, std_array)
    return ((row - mean_array) / std_array).astype(np.float32)


def nomination_features(details: dict, decoder: dict) -> np.ndarray:
    amount = float(details.get("amount") or 0.0)
    when = details.get("nomination_date") or datetime.now(timezone.utc)
    if isinstance(when, datetime):
        when = when.date()
    if not isinstance(when, date):
        when = date.fromisoformat(str(when)[:10])

    if decoder.get("feature_schema_version") != "gnn-v2":
        raise PermanentExtensionError("UNSUPPORTED_FEATURE_SCHEMA")
    category_stats = decoder.get("category_amount_stats") or {}
    robust = (category_stats.get("categories") or {}).get(
        str(int(details.get("category_id") or 0)),
        category_stats.get("global") or {"median": 0.0, "scale": 1.0},
    )
    robust_z = (amount - float(robust.get("median", 0.0))) / max(
        float(robust.get("scale", 1.0)), 1.0
    )
    dow_angle = 2.0 * math.pi * when.weekday() / 7.0
    month_angle = 2.0 * math.pi * (when.month - 1) / 12.0
    values = {
        "LogAmount": math.log1p(max(amount, 0.0)),
        "CategoryRelativeAmountRobustZScore": robust_z,
        "DaysBeforeGraphCutoff": 0.0,
        "DayOfWeekSin": math.sin(dow_angle),
        "DayOfWeekCos": math.cos(dow_angle),
        "MonthSin": math.sin(month_angle),
        "MonthCos": math.cos(month_angle),
        "HistoricalStatus": 0.0,
    }
    row = np.array(
        [[values.get(column, 0.0) for column in decoder["nomination_feature_columns"]]],
        dtype=np.float32,
    )
    return _standardise(
        row, decoder["nomination_scaler_mean"], decoder["nomination_scaler_std"]
    )


def _snapshot_inputs(snapshot: dict) -> tuple[dict, dict]:
    try:
        x_dict = dict(snapshot["node_features"])
        edge_index_dict = {
            (row["source"], row["relationship"], row["target"]): row["edge_index"]
            for row in snapshot["edges"]
        }
    except (KeyError, TypeError) as exc:
        raise PermanentExtensionError("INVALID_GRAPH_SNAPSHOT") from exc
    return x_dict, edge_index_dict


def _score(decoder_module, z_nom: np.ndarray, z_ben: np.ndarray, x_nom: np.ndarray) -> float:
    combined = np.concatenate(
        [z_nom.reshape(1, -1), z_ben.reshape(1, -1), x_nom], axis=1
    ).astype(np.float32)
    with torch.no_grad():
        return float(torch.sigmoid(decoder_module(torch.from_numpy(combined)).squeeze()))


def reproduce(
    *,
    bundle: ArtifactBundle,
    details: dict,
    gnn_result: dict,
    sql_embeddings: dict[int, np.ndarray],
    policy: ReproductionPolicy,
) -> ReproductionResult:
    decoder_module = build_decoder(bundle.decoder)
    x_nom = nomination_features(details, bundle.decoder)
    nominator_id = int(details["nominator_id"])
    beneficiary_id = int(details["beneficiary_id"])
    try:
        sql_nom = np.asarray(sql_embeddings[nominator_id], dtype=np.float32)
        sql_ben = np.asarray(sql_embeddings[beneficiary_id], dtype=np.float32)
    except KeyError as exc:
        raise PermanentExtensionError("VERSIONED_SERVING_EMBEDDING_MISSING") from exc
    serving_probability = _score(decoder_module, sql_nom, sql_ben, x_nom)
    try:
        stored_probability = float(gnn_result["fraud_prob"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PermanentExtensionError("STORED_GNN_PROBABILITY_MISSING") from exc
    stored_difference = abs(serving_probability - stored_probability)

    x_dict, edge_index_dict = _snapshot_inputs(bundle.snapshot)
    encoder_module = build_encoder(bundle.encoder, x_dict, edge_index_dict)
    with torch.no_grad():
        encoded_users = encoder_module(x_dict, edge_index_dict)["user"].cpu().numpy()
    user_ids = [int(value) for value in bundle.snapshot.get("mappings", {}).get("user_ids", [])]
    indexes = {user_id: index for index, user_id in enumerate(user_ids)}
    if nominator_id not in indexes or beneficiary_id not in indexes:
        raise PermanentExtensionError("EXPLANATION_ENDPOINT_NOT_IN_SNAPSHOT")
    graph_nom = encoded_users[indexes[nominator_id]].astype(np.float32)
    graph_ben = encoded_users[indexes[beneficiary_id]].astype(np.float32)
    embedding_difference = max(
        float(np.max(np.abs(graph_nom - sql_nom))),
        float(np.max(np.abs(graph_ben - sql_ben))),
    )
    reconstructed_probability = _score(decoder_module, graph_nom, graph_ben, x_nom)
    graph_difference = abs(reconstructed_probability - serving_probability)

    if (
        stored_difference > policy.probability_tolerance
        or graph_difference > policy.probability_tolerance
        or embedding_difference > policy.embedding_tolerance
    ):
        raise PermanentExtensionError(
            "SCORE_REPRODUCTION_MISMATCH:"
            f"stored={stored_difference:.8f},graph={graph_difference:.8f},"
            f"embedding={embedding_difference:.8f}"
        )
    return ReproductionResult(
        serving_probability=serving_probability,
        reconstructed_probability=reconstructed_probability,
        stored_probability_difference=stored_difference,
        graph_probability_difference=graph_difference,
        embedding_max_difference=embedding_difference,
    )
