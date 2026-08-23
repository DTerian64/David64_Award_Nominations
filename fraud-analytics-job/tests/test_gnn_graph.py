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
from datetime import date

import numpy as np
import pytest
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gnn_graph as G
from tests.synthetic import make_tenant, make_two_tenants


# ── Tenant isolation ──────────────────────────────────────────────────────────

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


def test_no_userGraphFlags_column_leaks_into_user_features():
    """Model-independence constraint, asserted mechanically rather than by review."""
    forbidden = {
        "IsInRing", "RingMaxUserCount", "RingMaxNominationCount", "IsSuperNominator",
        "IsInCopyPasteCluster", "CopyPasteClusterSize", "HasTransactionalLanguage",
        "IsApproverAffinity", "HighestSeverity", "PairApprovalCount",
        "GraphCycleFlag", "GraphReciprocalFlag", "GraphClusterSize",
        "SuperNominatorFlag", "TransactionalLanguageFlag", "ApproverAffinityFlag",
        "GraphApproverPairCount",
    }
    assert not (set(G.USER_FEATURE_COLUMNS) & forbidden)
    assert not (set(G.NOMINATION_FEATURE_COLUMNS) & forbidden)


def test_missing_approver_is_encoded_as_sentinel():
    users, noms, _ = make_tenant(1)
    for n in noms:
        n["ApproverId"] = None
    g = G.build_hetero_data(users, noms)
    assert g["data"]["user", "approves", "nomination"].edge_index.shape[1] == 0
    assert (g["train"]["triples"][:, 2] == -1).all()


# ── Embedding serialisation (round-trip into VARBINARY and back) ──────────────

def test_embedding_bytes_round_trip():
    rng = np.random.default_rng(0)
    emb = rng.standard_normal(64).astype(np.float32)
    blob = emb.tobytes()
    assert len(blob) == 64 * 4
    back = np.frombuffer(blob, dtype=np.float32)
    assert np.array_equal(emb, back)
