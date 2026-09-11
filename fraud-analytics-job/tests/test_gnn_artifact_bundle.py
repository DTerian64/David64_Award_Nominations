"""Tests for the immutable GNN v2 graph snapshot bundle.

Run: python -m pytest tests/test_gnn_artifact_bundle.py -v
"""

from modeling.gnn import artifact_bundle as bundle
from modeling.gnn import graph as gnn_graph
from tests.synthetic import make_tenant


def test_snapshot_round_trip_contains_only_one_tenant_and_exact_graph(tmp_path):
    users, nominations, _ = make_tenant(1)
    for i, row in enumerate(nominations):
        row["CategoryId"] = 10 + (i % 3)
    graph = gnn_graph.build_hetero_data(users, nominations)
    snapshot = bundle.build_snapshot(
        graph=graph,
        tenant_id=1,
        model_version="gnn-v2-test",
        graph_snapshot_id="gnn-graph-v2-test",
    )
    path = tmp_path / "graph_snapshot.pt"
    bundle.write_snapshot(path, snapshot)

    assert snapshot["tenant_id"] == 1
    assert snapshot["feature_schema_version"] == "gnn-v2"
    assert snapshot["graph_snapshot_id"] == "gnn-graph-v2-test"
    assert snapshot["mappings"]["user_ids"] == sorted(u["UserId"] for u in users)
    assert len(snapshot["mappings"]["nomination_ids"]) == graph["data"]["nomination"].num_nodes

    x_dict, edge_index_dict = bundle.snapshot_to_hetero_inputs(snapshot)
    assert set(x_dict) == {"user", "nomination", "category"}
    assert set(edge_index_dict) == set(graph["data"].edge_types)
    for relation, edge_index in edge_index_dict.items():
        assert edge_index.equal(graph["data"][relation].edge_index)


def test_snapshot_has_no_labels_or_other_engine_outputs(tmp_path):
    users, nominations, _ = make_tenant(1)
    graph = gnn_graph.build_hetero_data(users, nominations)
    snapshot = bundle.build_snapshot(
        graph=graph,
        tenant_id=1,
        model_version="gnn-v2-test",
        graph_snapshot_id="gnn-graph-v2-test",
    )
    flattened_keys = str(snapshot).lower()
    assert "isfraud" not in flattened_keys
    assert "rfscore" not in flattened_keys
    assert "usergraphflags" not in flattened_keys
    assert "trainingdisposition" not in flattened_keys
