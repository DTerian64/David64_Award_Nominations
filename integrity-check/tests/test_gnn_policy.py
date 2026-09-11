"""GNN ConfigurationJson serving-policy contract.

Run: python -m pytest tests/test_gnn_policy.py -v
"""

import json
import os
from contextlib import contextmanager
from unittest.mock import patch


os.environ.setdefault("SQL_SERVER", "test.invalid")
os.environ.setdefault("SQL_DATABASE", "test")

from utils import db


def _configuration() -> dict:
    return {
        "schema_version": 1,
        "model": {"hidden_dimension": 64, "embedding_dimension": 32},
        "training": {
            "epochs": 200,
            "rolling_fold_count": 4,
            "window_days": 180,
            "minimum_training_samples": 300,
            "minimum_users": 50,
            "minimum_positive_labels_per_split": 10,
        },
        "artifacts": {
            "embedding_retention_days": 90,
            "stale_embedding_days": 14,
        },
        "architecture_selection": {
            "candidate_architectures": ["graphsage", "gcn", "gatv2"],
            "selection_metric": "holdout_pr_auc",
            "minimum_improvement_over_mlp": 0.02,
            "incumbent_tie_tolerance": 0.01,
            "minimum_eligible_graph_candidates": 2,
        },
        "score_routing": {
            "low_threshold": 25,
            "medium_threshold": 45,
            "high_threshold": 65,
            "critical_threshold": 85,
        },
    }


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.query = None
        self.tenant_id = None

    def execute(self, query, tenant_id):
        self.query = query
        self.tenant_id = tenant_id

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.cursor_value = _Cursor(row)

    def cursor(self):
        return self.cursor_value


def test_configuration_json_is_expanded_for_live_inference():
    connection = _Connection(
        (41, 3, True, True, json.dumps(_configuration()), False, "MEDIUM")
    )

    @contextmanager
    def connection_context():
        yield connection

    with patch.object(db, "_get_conn", connection_context):
        policy = db.get_active_gnn_scoring_policy(7)

    assert policy["embed_dim"] == 32
    assert policy["candidate_architectures"] == ["graphsage", "gcn", "gatv2"]
    assert policy["thresholds"] == {
        "low": 25.0, "medium": 45.0, "high": 65.0, "critical": 85.0,
    }
    assert connection.cursor_value.tenant_id == 7
    assert "ConfigurationJson" in connection.cursor_value.query


def test_unknown_configuration_schema_is_rejected():
    configuration = _configuration()
    configuration["schema_version"] = 2
    connection = _Connection(
        (41, 3, True, True, json.dumps(configuration), False, "MEDIUM")
    )

    @contextmanager
    def connection_context():
        yield connection

    with patch.object(db, "_get_conn", connection_context):
        try:
            db.get_active_gnn_scoring_policy(7)
        except ValueError as exc:
            assert "schema_version 1" in str(exc)
        else:
            raise AssertionError("unknown configuration schema was accepted")
