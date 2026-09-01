"""
gnn_graph.py — per-tenant heterogeneous graph construction for the GNN stage
============================================================================
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
The graph uses three temporal windows:

    NomDate <  t_graph                message-passing graph
    t_graph <= NomDate < t_cut        training targets
    NomDate >= t_cut                  evaluation targets

An earlier two-way split left training targets inside the
message-passing graph — a nomination then participates in producing the
embeddings used to score it, and training metrics are inflated. The three-way
split removes that. Evaluation targets are out-of-graph under both schemes, so
the honest number the rollout gate depends on is unchanged; this only stops the
training number from flattering itself.

User behavioural features are computed from pre-t_graph nominations ONLY.
Computing them over the full window would leak post-cutoff activity into the
node features, which is the subtler half of the same leak.
"""

from __future__ import annotations

import logging
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

USER_FEATURE_COLUMNS = [
    "NominationsMade",
    "NominationsReceived",
    "NominationsApproved",
    "AvgAmountGiven",
    "StdAmountGiven",
    "AvgAmountReceived",
    "UniqueBeneficiaries",
    "UniqueNominators",
    "ConcentrationRatio",       # max nominations to any single beneficiary / total made
    "ReciprocalPairCount",      # pairs where the counterparty also nominated this user
]

NOMINATION_FEATURE_COLUMNS = [
    "Amount",
    "AmountZScore",
    "DayOfWeek",
    "Month",
    "IsWeekend",
    "IsHighAmount",
    "HasApprover",
]

EDGE_TYPES = [
    ("user", "nominates", "nomination"),
    ("nomination", "benefits", "user"),
    ("user", "approves", "nomination"),
]


# ── SQL ───────────────────────────────────────────────────────────────────────

def fetch_tenant_rows(conn, tenant_id: int, window_days: int) -> tuple[list[dict], list[dict]]:
    """
    Load users and nominations for one tenant, directly from the source tables.

    Tenant scoping mirrors graph_analytics._load_nominations(): the
    nomination is attributed to the NOMINATOR's tenant, because dbo.Nominations
    carries no TenantId of its own.

    That scoping is not airtight — a nomination whose beneficiary or approver
    belongs to a different tenant would drag a foreign user into the graph.
    assert_single_tenant() below exists to catch exactly that; it is called by
    build_hetero_data() on every run rather than left as a test-only check.

    Unlike the graph detector we do NOT restrict to Status IN ('Approved','Paid').
    The detectors care about committed spend; the GNN needs the rejected and
    pending population too, because rejections carry the fraud label.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT n.NominationId, n.NominatorId, n.BeneficiaryId, n.ApproverId,
               n.Status, n.Amount, n.NominationDate AS CreatedAt,
               n.ApprovedDate, n.PayedDate
        FROM   dbo.Nominations n
        JOIN   dbo.Users u ON u.UserId = n.NominatorId
        WHERE  u.TenantId = ?
          AND  n.NominationDate >= DATEADD(DAY, -?, GETDATE())
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
        for key in ("NominatorId", "BeneficiaryId", "ApproverId"):
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
    nominations: Sequence[dict], t_graph: date, t_cut: date
) -> tuple[list[dict], list[dict], list[dict]]:
    """Partition into (graph_edges, train_targets, eval_targets)."""
    graph_rows, train_rows, eval_rows = [], [], []
    for n in nominations:
        d = _as_date(n["CreatedAt"])
        if d < t_graph:
            graph_rows.append(n)
        elif d < t_cut:
            train_rows.append(n)
        else:
            eval_rows.append(n)
    return graph_rows, train_rows, eval_rows


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
    made         = defaultdict(int)
    received     = defaultdict(int)
    approved     = defaultdict(int)
    given_amts   = defaultdict(list)
    recv_amts    = defaultdict(list)
    beneficiaries = defaultdict(set)
    nominators    = defaultdict(set)
    pair_counts   = defaultdict(lambda: defaultdict(int))
    directed_pairs: set[tuple[int, int]] = set()

    for n in graph_rows:
        a, b = n["NominatorId"], n["BeneficiaryId"]
        amt = float(n.get("Amount") or 0.0)
        made[a] += 1
        received[b] += 1
        given_amts[a].append(amt)
        recv_amts[b].append(amt)
        beneficiaries[a].add(b)
        nominators[b].add(a)
        pair_counts[a][b] += 1
        directed_pairs.add((a, b))
        if n.get("ApproverId") is not None:
            approved[n["ApproverId"]] += 1

    rows = np.zeros((len(user_ids), len(USER_FEATURE_COLUMNS)), dtype=np.float32)
    for i, uid in enumerate(user_ids):
        g, r = given_amts[uid], recv_amts[uid]
        total_made = made[uid]
        top_pair = max(pair_counts[uid].values()) if pair_counts[uid] else 0
        reciprocal = sum(1 for b in beneficiaries[uid] if (b, uid) in directed_pairs)
        rows[i] = (
            total_made,
            received[uid],
            approved[uid],
            float(np.mean(g)) if g else 0.0,
            float(np.std(g)) if len(g) > 1 else 0.0,
            float(np.mean(r)) if r else 0.0,
            len(beneficiaries[uid]),
            len(nominators[uid]),
            (top_pair / total_made) if total_made else 0.0,
            reciprocal,
        )
    return rows


def build_nomination_features(
    rows: Sequence[dict], amount_mean: float, amount_std: float
) -> np.ndarray:
    """Per-nomination features. Consumed by the decoder, not by the encoder."""
    out = np.zeros((len(rows), len(NOMINATION_FEATURE_COLUMNS)), dtype=np.float32)
    hi = amount_mean + 2.0 * amount_std
    for i, n in enumerate(rows):
        d = _as_date(n["CreatedAt"])
        amt = float(n.get("Amount") or 0.0)
        z = (amt - amount_mean) / amount_std if amount_std > 0 else 0.0
        out[i] = (
            amt,
            z,
            d.weekday(),
            d.month,
            1.0 if d.weekday() >= 5 else 0.0,
            1.0 if amt > hi else 0.0,
            1.0 if n.get("ApproverId") is not None else 0.0,
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
) -> dict:
    """
    Build the message-passing graph plus the training and evaluation target sets.

    Returns a dict:
        data          HeteroData — users + pre-t_graph nominations only
        user_index    {UserId: row index in data['user'].x}
        train         {'nom_ids', 'x', 'triples'}   triples = (nom_idx, ben_idx, appr_idx)
        eval          same shape as train
        t_graph, t_cut
        amount_mean, amount_std

    Target nominations are NOT nodes in the graph. The decoder consumes their
    features directly, so a nomination never contributes to the embeddings used
    to score it.
    """
    assert_single_tenant(users, nominations)

    if t_graph is None or t_cut is None:
        t_graph, t_cut = temporal_thresholds(nominations, graph_quantile, cut_quantile)

    graph_rows, train_rows, eval_rows = split_targets(nominations, t_graph, t_cut)
    logger.info(
        "Temporal split — graph: %d, train targets: %d, eval targets: %d "
        "(t_graph=%s, t_cut=%s)",
        len(graph_rows), len(train_rows), len(eval_rows), t_graph, t_cut,
    )
    if not graph_rows:
        raise ValueError("Message-passing window is empty — widen the detection window.")

    user_ids = sorted({u["UserId"] for u in users})
    user_index = {uid: i for i, uid in enumerate(user_ids)}

    # Amount statistics from the graph window only — same leakage rule as the
    # user features. Matches the tenant-scoped z-score in train_rf_model.py.
    g_amounts = np.array([float(n.get("Amount") or 0.0) for n in graph_rows], dtype=np.float64)
    amount_mean = float(g_amounts.mean()) if g_amounts.size else 0.0
    amount_std = float(g_amounts.std()) if g_amounts.size > 1 else 0.0

    # ── Feature standardisation ───────────────────────────────────────────────
    # Mandatory, not cosmetic. Raw user features mix counts (~20) with currency
    # amounts (~500); mean-aggregation in SAGEConv then lets the amount columns
    # dominate every message and the encoder fails to train at all. Statistics
    # come from the graph window only, for the same leakage reason as everything
    # else here, and are persisted into the artifact so gnn_check.py can apply
    # the identical transform at inference.
    user_raw = build_user_features(user_ids, graph_rows)
    user_mean, user_std = _standardiser(user_raw)
    nom_raw_graph = build_nomination_features(graph_rows, amount_mean, amount_std)
    nom_mean, nom_std = _standardiser(nom_raw_graph)

    data = HeteroData()
    data["user"].x = torch.from_numpy(_apply(user_raw, user_mean, user_std))
    data["nomination"].x = torch.from_numpy(_apply(nom_raw_graph, nom_mean, nom_std))

    nom_index = {n["NominationId"]: i for i, n in enumerate(graph_rows)}

    nominates_src, nominates_dst = [], []
    benefits_src,  benefits_dst  = [], []
    approves_src,  approves_dst  = [], []
    for n in graph_rows:
        ni = nom_index[n["NominationId"]]
        nominates_src.append(user_index[n["NominatorId"]])
        nominates_dst.append(ni)
        benefits_src.append(ni)
        benefits_dst.append(user_index[n["BeneficiaryId"]])
        if n.get("ApproverId") is not None:
            approves_src.append(user_index[n["ApproverId"]])
            approves_dst.append(ni)

    def _ei(src, dst):
        return torch.tensor([src, dst], dtype=torch.long) if src else torch.zeros((2, 0), dtype=torch.long)

    data["user", "nominates", "nomination"].edge_index   = _ei(nominates_src, nominates_dst)
    data["nomination", "benefits", "user"].edge_index    = _ei(benefits_src, benefits_dst)
    data["user", "approves", "nomination"].edge_index    = _ei(approves_src, approves_dst)

    # Reverse relations so message passing is bidirectional. Without these,
    # a user node receives nothing from the nominations it participates in.
    data["nomination", "rev_nominates", "user"].edge_index = _ei(nominates_dst, nominates_src)
    data["user", "rev_benefits", "nomination"].edge_index  = _ei(benefits_dst, benefits_src)
    data["nomination", "rev_approves", "user"].edge_index  = _ei(approves_dst, approves_src)

    def _targets(rows: Sequence[dict]) -> dict:
        triples = []
        for n in rows:
            triples.append((
                user_index[n["NominatorId"]],
                user_index[n["BeneficiaryId"]],
                user_index.get(n["ApproverId"], -1) if n.get("ApproverId") is not None else -1,
            ))
        raw = build_nomination_features(rows, amount_mean, amount_std)
        return {
            "nom_ids": [n["NominationId"] for n in rows],
            "x": torch.from_numpy(_apply(raw, nom_mean, nom_std)),
            "triples": torch.tensor(triples, dtype=torch.long) if triples
                       else torch.zeros((0, 3), dtype=torch.long),
        }

    return {
        "data": data,
        "user_index": user_index,
        "train": _targets(train_rows),
        "eval": _targets(eval_rows),
        "t_graph": t_graph,
        "t_cut": t_cut,
        "amount_mean": amount_mean,
        "amount_std": amount_std,
        # Persisted into gnn_head_tenant_<id>.pt; gnn_check.py must apply these
        # exact values or inference silently scores in a different feature space.
        "user_scaler": {"mean": user_mean, "std": user_std},
        "nomination_scaler": {"mean": nom_mean, "std": nom_std},
    }
