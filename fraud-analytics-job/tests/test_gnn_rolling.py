"""Rolling-origin training and offline GNN candidate contract tests.

Run:  python -m pytest tests/test_gnn_rolling.py -v
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modeling.gnn import graph as G
from modeling.gnn.model import (
    CANDIDATE_ARCHITECTURES,
    build_candidate,
    train_candidate_rolling,
    train_gnn_rolling,
)
from tests.synthetic import make_tenant


def _rolling_fixture():
    users, nominations, labels = make_tenant(
        1,
        n_users=30,
        nominations_per_user=4,
        n_decoys=8,
    )
    label_map = {
        nomination["NominationId"]: label
        for nomination, label in zip(nominations, labels)
    }
    folds = G.build_rolling_folds(users, nominations, n_folds=3)
    y_train = [
        np.asarray([label_map[nid] for nid in fold["train"]["nom_ids"]])
        for fold in folds
    ]
    y_holdout = np.asarray(
        [label_map[nid] for nid in folds[-1]["eval"]["nom_ids"]]
    )
    return folds, y_train, y_holdout


def test_all_phase_2b_candidates_share_the_same_forward_contract():
    folds, _, _ = _rolling_fixture()
    graph = folds[-1]

    for architecture in CANDIDATE_ARCHITECTURES:
        model = build_candidate(
            architecture,
            graph,
            hidden_dim=8,
            emb_dim=8,
        )
        output = model(
            graph["data"], graph["train"]["pairs"], graph["train"]["x"]
        )
        assert output.shape == (len(graph["train"]["nom_ids"]),)


def test_rolling_mlp_never_uses_holdout_for_epoch_selection():
    folds, y_train, y_holdout = _rolling_fixture()

    _, metrics = train_candidate_rolling(
        folds,
        y_train,
        y_holdout,
        architecture="mlp",
        epochs=2,
        hidden_dim=8,
        emb_dim=8,
        log_every=0,
    )

    assert metrics["selection_policy"] == "fixed_epochs_final_holdout_untouched"
    assert metrics["epochs_run"] == 2
    assert all("eval_pr_auc" not in row for row in metrics["history"])
    assert metrics["n_train"] == sum(len(labels) for labels in y_train)
    assert metrics["n_eval"] == len(y_holdout)


def test_production_rolling_wrapper_keeps_graphsage_as_champion():
    folds, y_train, y_holdout = _rolling_fixture()

    model, metrics = train_gnn_rolling(
        folds,
        y_train,
        y_holdout,
        epochs=1,
        hidden_dim=8,
        emb_dim=8,
        log_every=0,
    )

    assert model.architecture == "graphsage"
    assert metrics["architecture"] == "graphsage"
    assert metrics["rolling_fold_count"] == 3


@pytest.mark.parametrize("architecture", ["gcn", "gatv2"])
def test_graph_challengers_train_under_the_same_rolling_contract(architecture):
    folds, y_train, y_holdout = _rolling_fixture()

    model, metrics = train_candidate_rolling(
        folds,
        y_train,
        y_holdout,
        architecture=architecture,
        epochs=1,
        hidden_dim=8,
        emb_dim=8,
        log_every=0,
    )

    assert model.architecture == architecture
    assert metrics["architecture"] == architecture
    assert metrics["n_eval"] == len(y_holdout)
    assert metrics["parameter_count"] > 0


def test_serving_graph_contains_all_eligible_history():
    users, nominations, _ = make_tenant(
        1,
        n_users=30,
        nominations_per_user=4,
        n_decoys=8,
    )
    latest = max(G._as_date(row["CreatedAt"]) for row in nominations)

    graph = G.build_serving_graph(users, nominations)

    assert len(graph["graph_nomination_ids"]) == len(nominations)
    assert graph["graph_snapshot_as_of"] == latest
    assert graph["train"]["nom_ids"] == []
    assert graph["eval"]["nom_ids"] == []
