import math

import numpy as np
import pytest
import torch
import torch.nn as nn

from extensions.gnn_explainer.artifacts import ArtifactBundle
from extensions.gnn_explainer.errors import PermanentExtensionError
from extensions.gnn_explainer.model import HeteroEncoder, RELATIONS
from extensions.gnn_explainer.reproduction import ReproductionPolicy, _calibrate, nomination_features, reproduce
from integrity_engine.gnn import CAUSAL_CONTEXT_FEATURE_COLUMNS


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


def test_v4_reproduction_applies_overall_head_calibration():
    decoder = {
        "model_schema_version": 4,
        "calibration": {"OVERALL": {"slope": 2.0, "intercept": 0.0}},
    }
    assert _calibrate(0.8, decoder) == pytest.approx(16 / 17)


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
                    "calibration": {"method": "PLATT", "slope": 0.5, "intercept": 0.25},
                    "category_amount_stats": {"global": {"median": 100., "scale": 20.}, "categories": {}}}
    details = {"amount": 125., "category_id": 4, "nomination_date": "2026-09-11",
               "nominator_id": 10, "beneficiary_id": 11}
    features = nomination_features(details, decoder_head)
    inputs = np.concatenate([encoded[0].numpy()[None, :], encoded[1].numpy()[None, :], features], axis=1)
    with torch.no_grad():
        raw_probability = float(torch.sigmoid(decoder(torch.from_numpy(inputs)).squeeze()))
    raw_logit = math.log(raw_probability / (1.0 - raw_probability))
    probability = 1.0 / (1.0 + math.exp(-(0.5 * raw_logit + 0.25)))
    result = reproduce(bundle=ArtifactBundle({}, snapshot, encoder_head, decoder_head), details=details,
                       gnn_result={"fraud_prob": round(probability, 4)},
                       sql_embeddings={10: encoded[0].numpy(), 11: encoded[1].numpy()},
                       policy=ReproductionPolicy())
    assert result.graph_probability_difference < 1e-7
    assert result.embedding_max_difference < 1e-7


def test_causal_feature_reproduction_requires_persisted_score_context():
    columns = [
        "LogAmount",
        "CategoryRelativeAmountRobustZScore",
        "DaysBeforeGraphCutoff",
        "DayOfWeekSin",
        "DayOfWeekCos",
        "MonthSin",
        "MonthCos",
        "HistoricalStatus",
        *CAUSAL_CONTEXT_FEATURE_COLUMNS,
    ]
    decoder = {
        "feature_schema_version": "gnn-v2-causal-v1",
        "nomination_feature_columns": columns,
        "nomination_scaler_mean": [0.0] * len(columns),
        "nomination_scaler_std": [1.0] * len(columns),
        "category_amount_stats": {
            "global": {"median": 100.0, "scale": 20.0},
            "categories": {},
        },
    }
    details = {"amount": 125.0, "category_id": 4, "nomination_date": "2026-09-11"}

    with pytest.raises(
        PermanentExtensionError, match="STORED_CAUSAL_CONTEXT_MISSING"
    ):
        nomination_features(details, decoder)

    context = {
        "features": {name: float(index) for index, name in enumerate(
            CAUSAL_CONTEXT_FEATURE_COLUMNS, start=1
        )}
    }
    values = nomination_features(details, decoder, context)[0]
    assert values[columns.index("LogPriorReversePairCount")] == 2.0
