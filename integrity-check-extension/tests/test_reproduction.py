import numpy as np
import pytest
import torch
import torch.nn as nn

from extensions.gnn_explainer.artifacts import ArtifactBundle
from extensions.gnn_explainer.model import HeteroEncoder, RELATIONS
from extensions.gnn_explainer.reproduction import ReproductionPolicy, nomination_features, reproduce


def graph_fixture():
    nodes = {"user": torch.tensor([[.2, -.3, .5], [.7, .1, -.2]]),
             "nomination": torch.tensor([[.1, .3], [-.4, .8]]),
             "category": torch.tensor([[.6]])}
    values = [[[0, 1], [0, 1]], [[0, 1], [1, 0]], [[0, 1], [0, 1]],
              [[1, 0], [0, 1]], [[0, 1], [0, 0]], [[0, 0], [0, 1]]]
    edges = {relation: torch.tensor(value) for relation, value in zip(RELATIONS, values)}
    snapshot = {
        "snapshot_schema_version": 1,
        "feature_schema_version": "gnn-v2", "tenant_id": 1,
        "model_version": "v1", "graph_snapshot_id": "s1", "node_features": nodes,
        "edges": [{"source": r[0], "relationship": r[1], "target": r[2], "edge_index": edges[r]} for r in RELATIONS],
        "mappings": {"user_ids": [10, 11]},
    }
    return snapshot, nodes, edges


@pytest.mark.parametrize("architecture", ["graphsage", "gcn", "gatv2"])
def test_supported_architecture_score_reproduction(architecture):
    torch.manual_seed(7)
    snapshot, nodes, edges = graph_fixture()
    encoder = HeteroEncoder(architecture, 4, 3, 2)
    with torch.no_grad():
        encoded = encoder(nodes, edges)["user"]
    encoder_head = {"encoder_state_dict": encoder.state_dict(), "architecture": architecture,
                    "model_version": "v1", "graph_snapshot_id": "s1", "feature_schema_version": "gnn-v2",
                    "hidden_dim": 4, "emb_dim": 3, "num_layers": 2}
    columns = ["LogAmount", "CategoryRelativeAmountRobustZScore", "DaysBeforeGraphCutoff",
               "DayOfWeekSin", "DayOfWeekCos", "MonthSin", "MonthCos", "HistoricalStatus"]
    decoder = nn.Sequential(nn.Linear(14, 5), nn.ReLU(), nn.Dropout(.2),
                            nn.Linear(5, 3), nn.ReLU(), nn.Linear(3, 1)).eval()
    decoder_head = {"decoder_state_dict": decoder.state_dict(), "decoder_hidden": [5, 3],
                    "architecture": architecture, "model_version": "v1", "graph_snapshot_id": "s1",
                    "feature_schema_version": "gnn-v2", "emb_dim": 3,
                    "nomination_feature_columns": columns, "nomination_scaler_mean": [0.] * 8,
                    "nomination_scaler_std": [1.] * 8,
                    "category_amount_stats": {"global": {"median": 100., "scale": 20.}, "categories": {}}}
    details = {"amount": 125., "category_id": 4, "nomination_date": "2026-09-11",
               "nominator_id": 10, "beneficiary_id": 11}
    features = nomination_features(details, decoder_head)
    inputs = np.concatenate([encoded[0].numpy()[None, :], encoded[1].numpy()[None, :], features], axis=1)
    with torch.no_grad():
        probability = float(torch.sigmoid(decoder(torch.from_numpy(inputs)).squeeze()))
    result = reproduce(bundle=ArtifactBundle({}, snapshot, encoder_head, decoder_head), details=details,
                       gnn_result={"fraud_prob": round(probability, 4)},
                       sql_embeddings={10: encoded[0].numpy(), 11: encoded[1].numpy()},
                       policy=ReproductionPolicy())
    assert result.graph_probability_difference < 1e-7
    assert result.embedding_max_difference < 1e-7
