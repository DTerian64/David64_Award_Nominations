"""Pure checks for the Synthetics Inc. configuration adapter.

Usage (from repository root)::

    python -m pytest scripts/synthetic_tenant/tests/test_database.py -v
"""

import json

from scripts.synthetic_tenant.database import _gnn_configuration, _sha256


def test_gnn_clone_changes_only_training_window():
    source = {
        "schema_version": 1,
        "model": {"hidden_dimension": 64},
        "training": {"window_days": 180, "epochs": 300},
        "score_routing": {"high_threshold": 65},
    }

    result = json.loads(_gnn_configuration(json.dumps(source)))

    assert result["training"]["window_days"] == 365
    source["training"]["window_days"] = 365
    assert result == source


def test_configuration_hash_is_independent_of_json_key_order():
    assert _sha256('{"a":1,"b":2}') == _sha256('{"b":2,"a":1}')

