"""Pure checks for the Synthetics Inc. configuration adapter.

Usage (from repository root)::

    python -m pytest scripts/synthetic_tenant/tests/test_database.py -v
"""

import json
from pathlib import Path

from scripts.synthetic_tenant import database
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


def test_rejected_imports_use_and_reconcile_the_hrbp_fraud_route():
    source = Path(database.__file__).read_text(encoding="utf-8")

    assert "CASE WHEN stage.Status = 'Rejected'" in source
    assert "THEN 'HRBP_REVIEW' ELSE 'MANAGER_APPROVAL' END" in source
    assert "CASE WHEN nomination.Status = 'Rejected'" in source
    assert "THEN 'FRAUD' ELSE NULL END" in source
