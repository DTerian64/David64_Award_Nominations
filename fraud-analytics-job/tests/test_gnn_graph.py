"""
test_gnn_graph.py — correctness guarantees for GNN graph construction
======================================================================
These cover the properties whose violation would be silent and expensive:
tenant bleed, temporal leakage, and embedding round-trip fidelity.

Run:  python -m pytest tests/test_gnn_graph.py -v
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import numpy as np
import pytest
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modeling.gnn import graph as G
from tests.synthetic import make_tenant, make_two_tenants


class _RecordingCursor:
    def __init__(self):
        self.calls = []
        self.description = []

    def execute(self, sql, *params):
        self.calls.append((sql, params))
        if "FROM   dbo.Nominations" in sql:
            self.description = [
                (name,) for name in (
                    "NominationId", "NominatorId", "BeneficiaryId", "Status",
                    "Amount", "CategoryId", "CreatedAt", "IsBehaviorEligible",
                )
            ]
        else:
            self.description = [(name,) for name in ("UserId", "TenantId", "ManagerId")]
        return self

    def fetchall(self):
        return []


class _RecordingConnection:
    def __init__(self):
        self.recording_cursor = _RecordingCursor()

    def cursor(self):
        return self.recording_cursor


# ── Tenant isolation ──────────────────────────────────────────────────────────

def test_loader_uses_p2p_behavior_statuses_and_canonical_label_targets():
    connection = _RecordingConnection()

    G.fetch_tenant_rows(connection, tenant_id=3, window_days=180)

    sql, params = connection.recording_cursor.calls[0]
    assert "n.Status IN ('Pending', 'Approved', 'Paid')" in sql
    assert "dbo.IntegrityDecisionResults" in sql
    assert "idr.TrainingDisposition IN ('FRAUD', 'LEGITIMATE')" in sql
    assert "ApproverId" not in sql
    assert params == (3, 180)

def test_single_tenant_fixture_passes_isolation_check():
    users, noms, _ = make_tenant(1)
    G.assert_single_tenant(users, noms)  # must not raise


def test_mixed_tenant_roster_is_rejected():
    (ua, na, _), (ub, nb, _) = make_two_tenants()
    with pytest.raises(ValueError, match="multiple tenants"):
        G.assert_single_tenant(ua + ub, na)


def test_nomination_referencing_a_foreign_user_is_rejected():
    """
    The real scoping risk. dbo.Nominations has no TenantId; rows are attributed
    via the NOMINATOR's tenant, so a nomination whose BENEFICIARY sits in
    another tenant would silently drag a foreign user into the graph.
    """
    (ua, na, _), (ub, nb, _) = make_two_tenants()
    leaked = dict(na[0])
    leaked["BeneficiaryId"] = ub[0]["UserId"]        # beneficiary from tenant 2
    with pytest.raises(ValueError, match="absent from the tenant roster"):
        G.assert_single_tenant(ua, na[1:] + [leaked])


def test_build_hetero_data_rejects_cross_tenant_rows():
    (ua, na, _), (ub, nb, _) = make_two_tenants()
    with pytest.raises(ValueError):
        G.build_hetero_data(ua + ub, na + nb)


# ── Temporal split ────────────────────────────────────────────────────────────

def test_message_passing_graph_contains_no_post_cutoff_nomination():
    users, noms, _ = make_tenant(1)
    g = G.build_hetero_data(users, noms)
    graph_rows, train_rows, eval_rows = G.split_targets(noms, g["t_graph"], g["t_cut"])

    assert len(graph_rows) > 0 and len(train_rows) > 0 and len(eval_rows) > 0
    assert g["data"]["nomination"].num_nodes == len(graph_rows)
    assert all(G._as_date(n["CreatedAt"]) < g["t_graph"] for n in graph_rows)


def test_target_nominations_are_not_nodes_in_the_graph():
    """A nomination must never contribute to the embeddings used to score it."""
    users, noms, _ = make_tenant(1)
    g = G.build_hetero_data(users, noms)
    graph_ids = {
        n["NominationId"] for n in noms if G._as_date(n["CreatedAt"]) < g["t_graph"]
    }
    assert not (set(g["train"]["nom_ids"]) & graph_ids)
    assert not (set(g["eval"]["nom_ids"]) & graph_ids)


def test_windows_are_disjoint_and_cover_everything():
    users, noms, _ = make_tenant(1)
    g = G.build_hetero_data(users, noms)
    graph_rows, train_rows, eval_rows = G.split_targets(noms, g["t_graph"], g["t_cut"])
    ids = [{n["NominationId"] for n in rows} for rows in (graph_rows, train_rows, eval_rows)]
    assert not (ids[0] & ids[1]) and not (ids[1] & ids[2]) and not (ids[0] & ids[2])
    assert ids[0] | ids[1] | ids[2] == {n["NominationId"] for n in noms}


def test_rolling_thresholds_cover_the_newest_date_in_final_holdout():
    rows = [
        {"CreatedAt": date(2026, 1, 1) + timedelta(days=index)}
        for index in range(10)
    ]

    folds = G.rolling_thresholds(rows, n_folds=3)

    assert len(folds) == 3
    assert all(t_graph < t_cut < eval_end for t_graph, t_cut, eval_end in folds)
    assert [folds[index][0] for index in range(1, 3)] == [
        folds[index][1] for index in range(2)
    ]
    assert folds[-1][2] == date(2026, 1, 11)


def test_rolling_folds_are_leak_free_and_training_windows_are_disjoint():
    users, noms, _ = make_tenant(
        1, n_users=30, nominations_per_user=4, n_decoys=8
    )
    folds = G.build_rolling_folds(users, noms, n_folds=3)

    train_id_sets = []
    for fold in folds:
        graph_ids = set(fold["graph_nomination_ids"])
        train_ids = set(fold["train"]["nom_ids"])
        eval_ids = set(fold["eval"]["nom_ids"])
        assert graph_ids.isdisjoint(train_ids | eval_ids)
        assert train_ids.isdisjoint(eval_ids)
        train_id_sets.append(train_ids)

    assert train_id_sets[0].isdisjoint(train_id_sets[1])
    assert train_id_sets[0].isdisjoint(train_id_sets[2])
    assert train_id_sets[1].isdisjoint(train_id_sets[2])
    assert max(G._as_date(row["CreatedAt"]) for row in noms) < folds[-1]["eval_end"]


def test_rolling_thresholds_reject_timeline_too_short_for_requested_folds():
    rows = [
        {"CreatedAt": date(2026, 1, 1) + timedelta(days=index)}
        for index in range(4)
    ]

    with pytest.raises(ValueError, match="at least 5 distinct dates"):
        G.rolling_thresholds(rows, n_folds=3)


def test_rejected_hrbp_label_is_target_only_not_message_passing_behavior():
    users, noms, _ = make_tenant(1)
    t_graph = date(2025, 10, 1)
    t_cut = date(2026, 1, 1)
    old_rejected = dict(noms[0], IsBehaviorEligible=False)
    old_rejected["CreatedAt"] = date(2025, 7, 1)
    recent_rejected = dict(noms[-1], IsBehaviorEligible=False)
    recent_rejected["CreatedAt"] = date(2026, 2, 1)

    graph_rows, train_rows, eval_rows = G.split_targets(
        [old_rejected, recent_rejected], t_graph, t_cut
    )

    assert graph_rows == []
    assert train_rows == []
    assert [row["NominationId"] for row in eval_rows] == [
        recent_rejected["NominationId"]
    ]


def test_user_features_ignore_post_graph_activity():
    """
    User features must be computed from the message-passing window only.
    Appending far-future nominations must not move a single feature value.
    """
    users, noms, _ = make_tenant(1)
    g1 = G.build_hetero_data(users, noms)

    future = []
    for i, n in enumerate(noms[:200]):
        m = dict(n)
        m["NominationId"] = 9_000_000 + i
        m["CreatedAt"] = date(2030, 1, 1)
        future.append(m)

    # Pin the thresholds so the split does not move under the added rows.
    g2 = G.build_hetero_data(users, noms + future, t_graph=g1["t_graph"], t_cut=g1["t_cut"])
    assert torch.equal(g1["data"]["user"].x, g2["data"]["user"].x)


# ── Graph structure ───────────────────────────────────────────────────────────

def test_edge_counts_and_reverse_relations_match():
    users, noms, _ = make_tenant(1)
    g = G.build_hetero_data(users, noms)
    d = g["data"]
    n_graph = d["nomination"].num_nodes
    assert d["user", "nominates", "nomination"].edge_index.shape[1] == n_graph
    assert d["nomination", "benefits", "user"].edge_index.shape[1] == n_graph
    for src, rel, dst in G.EDGE_TYPES:
        fwd = d[src, rel, dst].edge_index
        rev = d[dst, f"rev_{rel}", src].edge_index
        assert fwd.shape == rev.shape
        assert torch.equal(fwd[0], rev[1]) and torch.equal(fwd[1], rev[0])


def test_v2_adds_category_nodes_and_graph_native_features():
    users, noms, _ = make_tenant(1)
    for i, nomination in enumerate(noms):
        nomination["CategoryId"] = 10 + (i % 3)
    g = G.build_hetero_data(users, noms)
    assert G.FEATURE_SCHEMA_VERSION == "gnn-v2"
    assert G.USER_FEATURE_COLUMNS == [
        "LogNominationsMade",
        "LogNominationsReceived",
        "LogUniqueCounterparties",
    ]
    assert "ConcentrationRatio" not in G.USER_FEATURE_COLUMNS
    assert "ReciprocalPairCount" not in G.USER_FEATURE_COLUMNS
    assert g["data"]["category"].num_nodes == 3
    assert g["data"]["nomination", "belongs_to", "category"].edge_index.shape[1] == g["data"]["nomination"].num_nodes


def test_target_status_is_not_exposed_as_a_v2_feature():
    rows = [{
        "NominationId": 1,
        "NominatorId": 1,
        "BeneficiaryId": 2,
        "CategoryId": 10,
        "Amount": 500,
        "CreatedAt": date(2026, 1, 2),
        "Status": "Paid",
    }]
    stats = G.build_category_amount_stats(rows)
    features = G.build_nomination_features(
        rows, stats, date(2026, 1, 1), historical=False
    )
    status_index = G.NOMINATION_FEATURE_COLUMNS.index("HistoricalStatus")
    recency_index = G.NOMINATION_FEATURE_COLUMNS.index("DaysBeforeGraphCutoff")
    assert features[0, status_index] == 0.0
    assert features[0, recency_index] == 0.0


def test_no_userGraphFlags_column_leaks_into_user_features():
    """Model-independence constraint, asserted mechanically rather than by review."""
    forbidden = {
        "IsInRing", "RingMaxUserCount", "RingMaxNominationCount", "IsSuperNominator",
        "IsInCopyPasteCluster", "CopyPasteClusterSize",
        "IsApproverAffinity", "HighestSeverity", "PairApprovalCount",
        "GraphCycleFlag", "GraphReciprocalFlag", "GraphClusterSize",
        "SuperNominatorFlag", "ApproverAffinityFlag",
        "GraphApproverPairCount",
    }
    assert not (set(G.USER_FEATURE_COLUMNS) & forbidden)
    assert not (set(G.NOMINATION_FEATURE_COLUMNS) & forbidden)


def test_approver_behavior_is_not_encoded():
    users, noms, _ = make_tenant(1)
    g = G.build_hetero_data(users, noms)
    assert not any(relation[1] == "approves" for relation in G.EDGE_TYPES)
    assert "NominationsApproved" not in G.USER_FEATURE_COLUMNS
    assert "HasApprover" not in G.NOMINATION_FEATURE_COLUMNS
    assert g["train"]["pairs"].shape[1] == 2


# ── Embedding serialisation (round-trip into VARBINARY and back) ──────────────

def test_embedding_bytes_round_trip():
    rng = np.random.default_rng(0)
    emb = rng.standard_normal(64).astype(np.float32)
    blob = emb.tobytes()
    assert len(blob) == 64 * 4
    back = np.frombuffer(blob, dtype=np.float32)
    assert np.array_equal(emb, back)
