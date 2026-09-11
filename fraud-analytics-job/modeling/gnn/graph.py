"""
graph.py — per-tenant heterogeneous graph construction for the GNN stage
========================================================================
Stage 3 of the fraud-analytics-job pipeline.

Turns rows from dbo.Nominations / dbo.Users into a PyTorch Geometric
HeteroData object, and applies the temporal split that keeps message passing
from seeing the future.

Design constraints
------------------
1. Topology is read DIRECTLY from dbo.Nominations and dbo.Users. The
   NomGraph_Person / NomGraph_Nominated tables are a verbatim copy of the same
   data; reading them would create an ordering dependency on
   graph_analytics for no modelling benefit.

2. dbo.UserGraphFlags is NOT a feature source. The GNN must rediscover graph
   structure from raw topology. If it were handed the
   detectors' verdicts, its agreement with them would carry no information —
   which is the entire reason the model is being built.

Separation of concerns
----------------------
    fetch_tenant_rows()   SQL — untestable without a database
    build_hetero_data()   pure — fully testable from dicts

Everything below fetch_tenant_rows() is deterministic and free of I/O, so the
graph construction, the temporal split, and the tenant-isolation guarantee are
all unit-testable without a database connection.

Temporal split
--------------
Training uses rolling-origin folds. Each fold has three non-overlapping windows:

    NomDate <  t_graph                expanding message-passing graph
    t_graph <= NomDate < t_cut        training targets
    t_cut   <= NomDate < eval_end     evaluation targets

The training windows are disjoint across folds. The final evaluation interval
is the most-recent untouched holdout. Earlier evaluation intervals may be used
for architecture experiments, but never for selecting or fitting the deployed
model after they have entered a later fold's training interval.

User behavioural features are computed from pre-t_graph nominations ONLY.
Computing them over the full window would leak post-cutoff activity into the
node features, which is the subtler half of the same leak.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Sequence

import numpy as np
import torch
from torch_geometric.data import HeteroData

logger = logging.getLogger(__name__)


# ── Feature specs ─────────────────────────────────────────────────────────────
# Declared as module constants so modeling/train_gnn_model.py can persist them into the
# artifact and gnn_check.py can assert the inference-time layout matches.

FEATURE_SCHEMA_VERSION = "gnn-v2"

USER_FEATURE_COLUMNS = [
    "LogNominationsMade",
    "LogNominationsReceived",
    "LogUniqueCounterparties",
]

NOMINATION_FEATURE_COLUMNS = [
    "LogAmount",
    "CategoryRelativeAmountRobustZScore",
    "DaysBeforeGraphCutoff",
    "DayOfWeekSin",
    "DayOfWeekCos",
    "MonthSin",
    "MonthCos",
    "HistoricalStatus",
]

EDGE_TYPES = [
    ("user", "nominates", "nomination"),
    ("nomination", "benefits", "user"),
    ("nomination", "belongs_to", "category"),
]


# ── SQL ───────────────────────────────────────────────────────────────────────

def fetch_tenant_rows(conn, tenant_id: int, window_days: int) -> tuple[list[dict], list[dict]]:
    """
    Load users and nominations for one tenant, directly from the source tables.

    Tenant scoping mirrors graph_analytics._load_nominations(): the
    nomination is attributed to the NOMINATOR's tenant, because dbo.Nominations
    carries no TenantId of its own.

    That scoping is not airtight — a nomination whose beneficiary
    belongs to a different tenant would drag a foreign user into the graph.
    assert_single_tenant() below exists to catch exactly that; it is called by
    build_hetero_data() on every run rather than left as a test-only check.

    The operational topology contains Pending, Approved, and Paid nominations.
    HRBP-confirmed rejected outcomes are loaded only as supervised targets and
    are marked ineligible for message-passing behavior.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT n.NominationId, n.NominatorId, n.BeneficiaryId,
               n.Status, n.Amount, n.CategoryId, n.NominationDate AS CreatedAt,
               CASE WHEN n.Status IN ('Pending', 'Approved', 'Paid')
                    THEN 1 ELSE 0 END AS IsBehaviorEligible
        FROM   dbo.Nominations n
        JOIN   dbo.Users u ON u.UserId = n.NominatorId
        LEFT JOIN dbo.IntegrityDecisionResults idr
               ON idr.NominationId = n.NominationId
        WHERE  u.TenantId = ?
          AND  n.NominationDate >= DATEADD(DAY, -?, GETDATE())
          AND (
              n.Status IN ('Pending', 'Approved', 'Paid')
              OR idr.TrainingDisposition IN ('FRAUD', 'LEGITIMATE')
          )
    """, tenant_id, window_days)
    cols = [c[0] for c in cur.description]
    nominations = [dict(zip(cols, row)) for row in cur.fetchall()]

    cur.execute("""
        SELECT u.UserId, u.TenantId, u.ManagerId
        FROM   dbo.Users u
        WHERE  u.TenantId = ?
    """, tenant_id)
    cols = [c[0] for c in cur.description]
    users = [dict(zip(cols, row)) for row in cur.fetchall()]

    return users, nominations


# ── Helpers ───────────────────────────────────────────────────────────────────

def _as_date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v)).date()


def assert_single_tenant(users: Sequence[dict], nominations: Sequence[dict]) -> None:
    """
    Fail loudly if the row set spans more than one tenant, or if a nomination
    references a user outside the supplied roster.

    Multi-tenant bleed in a fraud graph is a data-isolation incident, not a
    modelling inconvenience: one tenant's embeddings would be computed partly
    from another tenant's nomination behaviour. It must abort the run.
    """
    tenant_ids = {u["TenantId"] for u in users if u.get("TenantId") is not None}
    if len(tenant_ids) > 1:
        raise ValueError(f"User roster spans multiple tenants: {sorted(tenant_ids)}")

    roster = {u["UserId"] for u in users}
    foreign: set[int] = set()
    for n in nominations:
        for key in ("NominatorId", "BeneficiaryId"):
            uid = n.get(key)
            if uid is not None and uid not in roster:
                foreign.add(uid)
    if foreign:
        raise ValueError(
            f"{len(foreign)} user id(s) referenced by nominations are absent from the "
            f"tenant roster (cross-tenant or orphaned): {sorted(foreign)[:10]}"
            + (" ..." if len(foreign) > 10 else "")
        )


def temporal_thresholds(
    nominations: Sequence[dict],
    graph_quantile: float = 0.60,
    cut_quantile: float = 0.80,
) -> tuple[date, date]:
    """Return (t_graph, t_cut) as date quantiles of the nomination timeline."""
    if not nominations:
        raise ValueError("Cannot derive temporal thresholds from an empty nomination set.")
    days = np.array([_as_date(n["CreatedAt"]).toordinal() for n in nominations])
    t_graph = date.fromordinal(int(np.quantile(days, graph_quantile)))
    t_cut = date.fromordinal(int(np.quantile(days, cut_quantile)))
    if t_cut <= t_graph:
        # Degenerate timeline (most nominations on one day). Widen by a day so the
        # windows stay non-empty rather than silently producing zero eval targets.
        t_cut = date.fromordinal(t_graph.toordinal() + 1)
    return t_graph, t_cut


def split_targets(
    nominations: Sequence[dict],
    t_graph: date,
    t_cut: date,
    eval_end: date | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Partition into (graph_edges, train_targets, eval_targets)."""
    graph_rows, train_rows, eval_rows = [], [], []
    for n in nominations:
        d = _as_date(n["CreatedAt"])
        if d < t_graph:
            if bool(n.get("IsBehaviorEligible", True)):
                graph_rows.append(n)
        elif d < t_cut:
            train_rows.append(n)
        elif eval_end is None or d < eval_end:
            eval_rows.append(n)
    return graph_rows, train_rows, eval_rows


def rolling_thresholds(
    nominations: Sequence[dict], n_folds: int = 3
) -> list[tuple[date, date, date]]:
    """Return expanding-history rolling-origin fold boundaries.

    ``n_folds + 2`` chronological segments are used. For each successive fold,
    graph history expands by one segment, followed by one train segment and one
    evaluation segment. The final fold's evaluation segment is therefore the
    untouched most-recent holdout.
    """
    if n_folds < 1:
        raise ValueError("n_folds must be at least 1")
    unique_days = sorted({_as_date(row["CreatedAt"]) for row in nominations})
    segment_count = n_folds + 2
    if len(unique_days) < segment_count:
        raise ValueError(
            f"Rolling evaluation needs at least {segment_count} distinct dates; "
            f"found {len(unique_days)}"
        )

    # Interior boundaries are starts of chronological segments. The final
    # boundary is exclusive and one day beyond the newest observation, so the
    # latest date is included in the honest holdout rather than silently lost.
    boundaries = [
        unique_days[(len(unique_days) * i) // segment_count]
        for i in range(1, segment_count)
    ]
    boundaries.append(date.fromordinal(unique_days[-1].toordinal() + 1))

    return [
        (boundaries[i], boundaries[i + 1], boundaries[i + 2])
        for i in range(n_folds)
    ]


def build_rolling_folds(
    users: Sequence[dict],
    nominations: Sequence[dict],
    n_folds: int = 3,
) -> list[dict]:
    """Build graph/train/evaluation snapshots for rolling-origin training."""
    behavior_rows = [
        row for row in nominations if bool(row.get("IsBehaviorEligible", True))
    ]
    folds = []
    for index, (t_graph, t_cut, eval_end) in enumerate(
        rolling_thresholds(behavior_rows, n_folds), start=1
    ):
        fold = build_hetero_data(
            users,
            nominations,
            t_graph=t_graph,
            t_cut=t_cut,
            eval_end=eval_end,
        )
        fold["fold_index"] = index
        fold["eval_end"] = eval_end
        folds.append(fold)
    return folds


def build_serving_graph(
    users: Sequence[dict],
    nominations: Sequence[dict],
) -> dict:
    """Build the deployment snapshot from all currently eligible behavior."""
    behavior_rows = [
        row for row in nominations if bool(row.get("IsBehaviorEligible", True))
    ]
    if not behavior_rows:
        raise ValueError("Cannot build a serving graph without eligible behavior.")
    snapshot_as_of = max(_as_date(row["CreatedAt"]) for row in behavior_rows)
    exclusive_cutoff = date.fromordinal(snapshot_as_of.toordinal() + 1)
    graph = build_hetero_data(
        users,
        nominations,
        t_graph=exclusive_cutoff,
        t_cut=date.fromordinal(exclusive_cutoff.toordinal() + 1),
        eval_end=date.fromordinal(exclusive_cutoff.toordinal() + 2),
    )
    graph["graph_snapshot_as_of"] = snapshot_as_of
    return graph


# ── Feature engineering ───────────────────────────────────────────────────────

def _standardiser(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Column mean/std, with zero-variance columns pinned to std=1 to avoid nan."""
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std < 1e-8] = 1.0
    return mean.astype(np.float32), std.astype(np.float32)


def _apply(x: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    return ((x - mean) / std).astype(np.float32)


def build_user_features(
    user_ids: Sequence[int], graph_rows: Sequence[dict]
) -> np.ndarray:
    """
    Behavioural aggregates over the MESSAGE-PASSING window only.

    Deliberately excludes every dbo.UserGraphFlags column.
    """
    made = defaultdict(int)
    received = defaultdict(int)
    counterparties = defaultdict(set)

    for n in graph_rows:
        a, b = n["NominatorId"], n["BeneficiaryId"]
        made[a] += 1
        received[b] += 1
        counterparties[a].add(b)
        counterparties[b].add(a)

    rows = np.zeros((len(user_ids), len(USER_FEATURE_COLUMNS)), dtype=np.float32)
    for i, uid in enumerate(user_ids):
        rows[i] = (
            math.log1p(made[uid]),
            math.log1p(received[uid]),
            math.log1p(len(counterparties[uid])),
        )
    return rows


def build_category_amount_stats(rows: Sequence[dict]) -> dict:
    """Fit robust category-relative amount statistics on graph history only."""
    grouped: dict[int, list[float]] = defaultdict(list)
    all_amounts: list[float] = []
    for row in rows:
        category_id = int(row.get("CategoryId") or 0)
        amount = float(row.get("Amount") or 0.0)
        grouped[category_id].append(amount)
        all_amounts.append(amount)

    def _stats(values: Sequence[float]) -> dict[str, float]:
        arr = np.asarray(values, dtype=np.float64)
        median = float(np.median(arr)) if arr.size else 0.0
        mad = float(np.median(np.abs(arr - median))) if arr.size else 0.0
        robust_scale = max(1.4826 * mad, 1.0)
        return {"median": median, "scale": robust_scale}

    return {
        "global": _stats(all_amounts),
        "categories": {
            str(category_id): _stats(values)
            for category_id, values in sorted(grouped.items())
        },
    }


def build_nomination_features(
    rows: Sequence[dict],
    category_amount_stats: dict,
    graph_cutoff: date,
    *,
    historical: bool,
) -> np.ndarray:
    """Build the graph-native v2 nomination attributes without future state."""
    out = np.zeros((len(rows), len(NOMINATION_FEATURE_COLUMNS)), dtype=np.float32)
    status_code = {"Pending": 0.0, "Approved": 1.0, "Paid": 2.0}
    category_stats = category_amount_stats.get("categories", {})
    global_stats = category_amount_stats.get("global", {"median": 0.0, "scale": 1.0})
    for i, n in enumerate(rows):
        d = _as_date(n["CreatedAt"])
        amt = float(n.get("Amount") or 0.0)
        stats = category_stats.get(str(int(n.get("CategoryId") or 0)), global_stats)
        robust_z = (amt - float(stats["median"])) / max(float(stats["scale"]), 1.0)
        dow_angle = 2.0 * math.pi * d.weekday() / 7.0
        month_angle = 2.0 * math.pi * (d.month - 1) / 12.0
        out[i] = (
            math.log1p(max(amt, 0.0)),
            robust_z,
            float(max((graph_cutoff - d).days, 0)) if historical else 0.0,
            math.sin(dow_angle),
            math.cos(dow_angle),
            math.sin(month_angle),
            math.cos(month_angle),
            status_code.get(str(n.get("Status")), 0.0) if historical else 0.0,
        )
    return out


# ── Graph assembly ────────────────────────────────────────────────────────────

def build_hetero_data(
    users: Sequence[dict],
    nominations: Sequence[dict],
    t_graph: date | None = None,
    t_cut: date | None = None,
    graph_quantile: float = 0.60,
    cut_quantile: float = 0.80,
    eval_end: date | None = None,
) -> dict:
    """
    Build the message-passing graph plus the training and evaluation target sets.

    Returns a dict:
        data          HeteroData — users + pre-t_graph nominations only
        user_index    {UserId: row index in data['user'].x}
        train         {'nom_ids', 'x', 'pairs'}   pairs = (nominator_idx, beneficiary_idx)
        eval          same shape as train
        t_graph, t_cut
        amount_mean, amount_std

    Target nominations are NOT nodes in the graph. The decoder consumes their
    features directly, so a nomination never contributes to the embeddings used
    to score it.
    """
    assert_single_tenant(users, nominations)

    if t_graph is None or t_cut is None:
        behavior_rows = [
            row for row in nominations
            if bool(row.get("IsBehaviorEligible", True))
        ]
        t_graph, t_cut = temporal_thresholds(
            behavior_rows, graph_quantile, cut_quantile
        )

    graph_rows, train_rows, eval_rows = split_targets(
        nominations, t_graph, t_cut, eval_end
    )
    logger.info(
        "Temporal split — graph: %d, train targets: %d, eval targets: %d "
        "(t_graph=%s, t_cut=%s)",
        len(graph_rows), len(train_rows), len(eval_rows), t_graph, t_cut,
    )
    if not graph_rows:
        raise ValueError("Message-passing window is empty — widen the detection window.")

    user_ids = sorted({u["UserId"] for u in users})
    user_index = {uid: i for i, uid in enumerate(user_ids)}

    # Amount statistics from graph history only. Mean/std remain in the return
    # contract for v1 compatibility diagnostics; v2 uses robust category stats.
    g_amounts = np.array([float(n.get("Amount") or 0.0) for n in graph_rows], dtype=np.float64)
    amount_mean = float(g_amounts.mean()) if g_amounts.size else 0.0
    amount_std = float(g_amounts.std()) if g_amounts.size > 1 else 0.0
    category_amount_stats = build_category_amount_stats(graph_rows)

    # ── Feature standardisation ───────────────────────────────────────────────
    # Mandatory, not cosmetic. Raw user features mix counts (~20) with currency
    # amounts (~500); mean-aggregation in SAGEConv then lets the amount columns
    # dominate every message and the encoder fails to train at all. Statistics
    # come from the graph window only, for the same leakage reason as everything
    # else here, and are persisted into the artifact so gnn_check.py can apply
    # the identical transform at inference.
    user_raw = build_user_features(user_ids, graph_rows)
    user_mean, user_std = _standardiser(user_raw)
    nom_raw_graph = build_nomination_features(
        graph_rows, category_amount_stats, t_graph, historical=True
    )
    nom_mean, nom_std = _standardiser(nom_raw_graph)

    data = HeteroData()
    data["user"].x = torch.from_numpy(_apply(user_raw, user_mean, user_std))
    data["nomination"].x = torch.from_numpy(_apply(nom_raw_graph, nom_mean, nom_std))

    nom_index = {n["NominationId"]: i for i, n in enumerate(graph_rows)}
    category_ids = sorted({int(n.get("CategoryId") or 0) for n in graph_rows})
    category_index = {category_id: i for i, category_id in enumerate(category_ids)}
    data["category"].x = torch.ones((len(category_ids), 1), dtype=torch.float32)

    nominates_src, nominates_dst = [], []
    benefits_src,  benefits_dst  = [], []
    belongs_src, belongs_dst = [], []
    for n in graph_rows:
        ni = nom_index[n["NominationId"]]
        nominates_src.append(user_index[n["NominatorId"]])
        nominates_dst.append(ni)
        benefits_src.append(ni)
        benefits_dst.append(user_index[n["BeneficiaryId"]])
        belongs_src.append(ni)
        belongs_dst.append(category_index[int(n.get("CategoryId") or 0)])

    def _ei(src, dst):
        return torch.tensor([src, dst], dtype=torch.long) if src else torch.zeros((2, 0), dtype=torch.long)

    data["user", "nominates", "nomination"].edge_index   = _ei(nominates_src, nominates_dst)
    data["nomination", "benefits", "user"].edge_index    = _ei(benefits_src, benefits_dst)
    data["nomination", "belongs_to", "category"].edge_index = _ei(belongs_src, belongs_dst)

    # Reverse relations so message passing is bidirectional. Without these,
    # a user node receives nothing from the nominations it participates in.
    data["nomination", "rev_nominates", "user"].edge_index = _ei(nominates_dst, nominates_src)
    data["user", "rev_benefits", "nomination"].edge_index  = _ei(benefits_dst, benefits_src)
    data["category", "rev_belongs_to", "nomination"].edge_index = _ei(belongs_dst, belongs_src)

    def _targets(rows: Sequence[dict]) -> dict:
        pairs = []
        for n in rows:
            pairs.append((
                user_index[n["NominatorId"]],
                user_index[n["BeneficiaryId"]],
            ))
        raw = build_nomination_features(
            rows, category_amount_stats, t_graph, historical=False
        )
        return {
            "nom_ids": [n["NominationId"] for n in rows],
            "x": torch.from_numpy(_apply(raw, nom_mean, nom_std)),
            "pairs": torch.tensor(pairs, dtype=torch.long) if pairs
                     else torch.zeros((0, 2), dtype=torch.long),
        }

    return {
        "data": data,
        "user_index": user_index,
        "category_index": category_index,
        "graph_nomination_ids": [n["NominationId"] for n in graph_rows],
        "train": _targets(train_rows),
        "eval": _targets(eval_rows),
        "t_graph": t_graph,
        "t_cut": t_cut,
        "eval_end": eval_end,
        "amount_mean": amount_mean,
        "amount_std": amount_std,
        "category_amount_stats": category_amount_stats,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "user_feature_columns": list(USER_FEATURE_COLUMNS),
        "nomination_feature_columns": list(NOMINATION_FEATURE_COLUMNS),
        # Persisted into gnn_head_tenant_<id>.pt; gnn_check.py must apply these
        # exact values or inference silently scores in a different feature space.
        "user_scaler": {"mean": user_mean, "std": user_std},
        "nomination_scaler": {"mean": nom_mean, "std": nom_std},
    }
