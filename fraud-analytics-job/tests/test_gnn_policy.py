"""Tenant-owned GNN policy loading contract.

Run:  python -m pytest tests/test_gnn_policy.py -v
"""

import json

from modeling.gnn.policy import load_active_policy


class Cursor:
    def __init__(self, row):
        self.row = row
        self.query = None
        self.tenant_id = None

    def execute(self, query, tenant_id):
        self.query = query
        self.tenant_id = tenant_id

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, row):
        self.value = Cursor(row)

    def cursor(self):
        return self.value


def _configuration(candidates=None):
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
            "candidate_architectures": candidates
            or ["graphsage", "gcn", "gatv2"],
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


def _row(candidates=None, configuration=None):
    return (
        41, 3, True, True,
        json.dumps(configuration or _configuration(candidates)),
        False, "MEDIUM",
    )


def test_loads_active_tenant_policy_without_environment_fallback():
    connection = Connection(_row())

    policy = load_active_policy(connection, 7)

    assert policy.policy_id == 41
    assert policy.policy_version == 3
    assert policy.candidate_architectures == ("graphsage", "gcn", "gatv2")
    assert policy.embed_dim == 32
    assert connection.value.tenant_id == 7
    assert "Status = 'ACTIVE'" in connection.value.query


def test_returns_none_when_tenant_has_no_active_policy():
    assert load_active_policy(Connection(None), 7) is None


def test_rejects_unsupported_candidate_architecture():
    try:
        load_active_policy(Connection(_row(["graphsage", "unknown"])), 7)
    except ValueError as exc:
        assert "unsupported architecture" in str(exc)
    else:
        raise AssertionError("invalid policy was accepted")


def test_rejects_unknown_configuration_schema():
    configuration = _configuration()
    configuration["schema_version"] = 2
    try:
        load_active_policy(Connection(_row(configuration=configuration)), 7)
    except ValueError as exc:
        assert "schema_version 1" in str(exc)
    else:
        raise AssertionError("unknown configuration schema was accepted")
