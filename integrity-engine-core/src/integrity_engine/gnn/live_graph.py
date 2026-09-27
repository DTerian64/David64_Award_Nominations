"""Causal live graph inputs, independent of SQL, blob storage and other engines.

Weights/scalers are frozen. Topology and behavioral aggregates are refreshed at
the target's strict (timestamp, id) boundary. There is no sampling or hidden cap.
"""
from dataclasses import dataclass
from bisect import bisect_left
from datetime import timedelta
import numpy as np
import torch

from .causal_context import _order_key, _timestamp
from .features import (
    USER_FEATURE_COLUMNS, NOMINATION_FEATURE_COLUMNS,
    build_user_features, build_nomination_features,
)

LIVE_ENCODING_CONTRACT = "gnn-live-causal-encoder-v1"


def target_causal_history(history, target, window_days):
    """Exact feature dependency edges for one target; never a sampled history."""
    boundary = _order_key(target)
    cutoff = boundary[0] - timedelta(days=window_days)
    rows = [row for row in history if bool(row.get("IsBehaviorEligible", True))
            and cutoff <= _timestamp(row["CreatedAt"]) and _order_key(row) < boundary]
    a, b = int(target["NominatorId"]), int(target["BeneficiaryId"])
    first = {int(row["BeneficiaryId"]) for row in rows if int(row["NominatorId"]) == b}
    last = {int(row["NominatorId"]) for row in rows if int(row["BeneficiaryId"]) == a}
    return [row for row in rows if int(row["NominatorId"]) in (a, b)
            or int(row["BeneficiaryId"]) in (a, b)
            or (int(row["NominatorId"]) in first and int(row["BeneficiaryId"]) in last)]


@dataclass
class LiveGraphInputs:
    x_dict: dict
    edge_index_dict: dict
    user_index: dict
    nomination_ids: list
    category_index: dict
    history_nomination_count: int


def preprocessing_from_graph(graph):
    return {
        "user_feature_columns": list(graph["user_feature_columns"]),
        "nomination_feature_columns": list(graph["nomination_feature_columns"]),
        "user_scaler_mean": graph["user_scaler"]["mean"],
        "user_scaler_std": graph["user_scaler"]["std"],
        "nomination_scaler_mean": graph["nomination_scaler"]["mean"],
        "nomination_scaler_std": graph["nomination_scaler"]["std"],
        "category_amount_stats": graph["category_amount_stats"],
        "causal_context_window_days": graph["causal_context_window_days"],
    }


def _scaled(values, mean, std):
    mean, std = np.asarray(mean, dtype=np.float32), np.asarray(std, dtype=np.float32)
    if mean.shape != (values.shape[1],) or std.shape != mean.shape:
        raise ValueError("Live encoder scaler dimensions do not match the feature contract")
    if not np.isfinite(mean).all() or not np.isfinite(std).all() or (std <= 0).any():
        raise ValueError("Invalid live encoder scalers")
    result = ((values - mean) / std).astype(np.float32)
    if not np.isfinite(result).all():
        raise ValueError("Non-finite live encoder inputs")
    return torch.from_numpy(result)


def build_live_graph_inputs(users, history, target, preprocessing, *, num_layers=None,
                            _historical_features=None):
    """Exact encoder dependency graph. Every target/future edge is excluded.

    Feature aggregates always use full causal history, even when message
    passing needs only a local dependency closure. No neighbors are sampled.
    """
    if preprocessing["user_feature_columns"] != USER_FEATURE_COLUMNS:
        raise ValueError("Unsupported live encoder user features")
    if preprocessing["nomination_feature_columns"] != NOMINATION_FEATURE_COLUMNS:
        raise ValueError("Unsupported live encoder nomination features")
    days = int(preprocessing["causal_context_window_days"])
    if days < 1:
        raise ValueError("Invalid live encoder history window")
    boundary = _order_key(target)
    cutoff = boundary[0] - timedelta(days=days)
    tenants = {int(row["TenantId"]) for row in users if row.get("TenantId") is not None}
    if len(tenants) > 1:
        raise ValueError("Live graph roster spans tenants")
    user_ids = sorted({int(row["UserId"]) for row in users})
    user_index = {uid: i for i, uid in enumerate(user_ids)}
    for endpoint in (target["NominatorId"], target["BeneficiaryId"]):
        if int(endpoint) not in user_index:
            raise ValueError("Live graph target references a user outside its tenant")
    rows = sorted((row for row in history
                   if bool(row.get("IsBehaviorEligible", True))
                   and cutoff <= _timestamp(row["CreatedAt"])
                   and _order_key(row) < boundary), key=_order_key)
    if len({int(row["NominationId"]) for row in rows}) != len(rows):
        raise ValueError("Duplicate nominations in live graph")
    if any(int(row[key]) not in user_index for row in rows
           for key in ("NominatorId", "BeneficiaryId")):
        raise ValueError("Live graph history references a user outside its tenant")
    all_rows = rows
    if num_layers is not None:
        if num_layers < 1:
            raise ValueError("Invalid live encoder depth")
        selected_users = {int(target["NominatorId"]), int(target["BeneficiaryId"])}
        selected_nominations, selected_categories = set(), set()
        frontier_users, frontier_nominations, frontier_categories = selected_users.copy(), set(), set()
        for _ in range(num_layers):
            new_nominations = {int(row["NominationId"]) for row in all_rows
                               if int(row["NominatorId"]) in frontier_users
                               or int(row["BeneficiaryId"]) in frontier_users
                               or int(row.get("CategoryId") or 0) in frontier_categories}
            neighboring = [row for row in all_rows if int(row["NominationId"]) in frontier_nominations]
            new_users = {int(row[key]) for row in neighboring for key in ("NominatorId", "BeneficiaryId")}
            new_categories = {int(row.get("CategoryId") or 0) for row in neighboring}
            frontier_users = new_users - selected_users
            frontier_nominations = new_nominations - selected_nominations
            frontier_categories = new_categories - selected_categories
            selected_users |= new_users
            selected_nominations |= new_nominations
            selected_categories |= new_categories
        row_indices = [i for i, row in enumerate(all_rows) if int(row["NominationId"]) in selected_nominations]
        rows = [all_rows[i] for i in row_indices]
        # Include boundary node attributes; edges beyond the dependency radius
        # cannot affect endpoint outputs within this encoder's layer count.
        selected_users |= {int(row[key]) for row in rows for key in ("NominatorId", "BeneficiaryId")}
        user_ids = sorted(selected_users)
        user_index = {uid: i for i, uid in enumerate(user_ids)}
        if _historical_features is not None:
            _historical_features = _historical_features[row_indices]
    categories = sorted({int(row.get("CategoryId") or 0) for row in rows})
    # Preserve all relation types even when there are no historical edges.
    category_index = {key: i for i, key in enumerate(categories)}
    nomination_raw = (build_nomination_features(
        rows, preprocessing["category_amount_stats"], boundary[0].date(),
        historical=True, context_rows=all_rows, causal_window_days=days,
    ) if _historical_features is None else _historical_features)
    if nomination_raw.shape != (len(rows), len(NOMINATION_FEATURE_COLUMNS)):
        raise ValueError("Historical feature cache does not match the causal graph")
    x_dict = {
        "user": _scaled(build_user_features(user_ids, all_rows),
                        preprocessing["user_scaler_mean"], preprocessing["user_scaler_std"]),
        "nomination": _scaled(nomination_raw, preprocessing["nomination_scaler_mean"],
                              preprocessing["nomination_scaler_std"]),
        "category": torch.ones((len(categories), 1), dtype=torch.float32),
    }
    source = [user_index[int(row["NominatorId"])] for row in rows]
    destination = [user_index[int(row["BeneficiaryId"])] for row in rows]
    nomination = list(range(len(rows)))
    category = [category_index[int(row.get("CategoryId") or 0)] for row in rows]
    def edges(a, b):
        return torch.tensor([a, b], dtype=torch.long).reshape(2, -1)
    relations = {
        ("user", "nominates", "nomination"): edges(source, nomination),
        ("nomination", "benefits", "user"): edges(nomination, destination),
        ("nomination", "rev_nominates", "user"): edges(nomination, source),
        ("user", "rev_benefits", "nomination"): edges(destination, nomination),
        ("nomination", "belongs_to", "category"): edges(nomination, category),
        ("category", "rev_belongs_to", "nomination"): edges(category, nomination),
    }
    return LiveGraphInputs(x_dict, relations, user_index,
                           [int(row["NominationId"]) for row in rows], category_index, len(all_rows))


class LiveGraphReplay:
    """Reuse causal historical node features during offline chronological replay.

    Each node's topology features use only its own prior edges. Precomputing
    them is safe; neither future nodes nor future aggregates enter the target
    graph. Recompute when the target's lower window boundary expires history.
    Only one feature matrix is retained, not a graph for every target.
    """
    def __init__(self, users, history, preprocessing, num_layers=None):
        self.users, self.preprocessing = users, preprocessing
        self.num_layers = num_layers
        self.history = sorted((row for row in history
                               if bool(row.get("IsBehaviorEligible", True))), key=_order_key)
        self.keys = [_order_key(row) for row in self.history]
        self.times = [key[0] for key in self.keys]
        self.cached_start, self.raw = None, None

    def build(self, target):
        boundary = _order_key(target)
        days = int(self.preprocessing["causal_context_window_days"])
        start = bisect_left(self.times, boundary[0] - timedelta(days=days))
        end = bisect_left(self.keys, boundary)
        if self.cached_start != start:
            suffix = self.history[start:]
            self.raw = build_nomination_features(
                suffix, self.preprocessing["category_amount_stats"], boundary[0].date(),
                historical=True, context_rows=suffix, causal_window_days=days,
            )
            self.cached_start = start
        rows = self.history[start:end]
        raw = self.raw[:len(rows)].copy()
        raw[:, 2] = [max((boundary[0].date() - _timestamp(row["CreatedAt"]).date()).days, 0)
                     for row in rows]
        return build_live_graph_inputs(self.users, rows, target, self.preprocessing,
                                       num_layers=self.num_layers, _historical_features=raw)
