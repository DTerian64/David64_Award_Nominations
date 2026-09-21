"""Pure checks for the Synthetics Inc. configuration adapter.

Usage (from repository root)::

    python -m pytest scripts/synthetic_tenant/tests/test_database.py -v
"""

import json
from datetime import date
from pathlib import Path

from scripts.synthetic_tenant import database
from scripts.synthetic_tenant.database import (
    _corpus_stage_values,
    _gnn_configuration,
    _sha256,
)
from scripts.synthetic_tenant.scenarios import generate_nominations, generate_users
from scripts.synthetic_tenant.scenarios import DIRECTORY_SEED


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


def test_corpus_stage_metadata_preserves_causal_scenario_contract():
    users = generate_users(DIRECTORY_SEED)
    nomination = next(
        row
        for row in generate_nominations(users, 20260921, date(2026, 9, 22))
        if row.scenario_id is not None
    )
    user_ids = {
        logical_id: index
        for index, logical_id in enumerate(
            {
                nomination.nominator_logical_id,
                nomination.beneficiary_logical_id,
                nomination.approver_logical_id,
            },
            start=1,
        )
    }

    values = _corpus_stage_values(
        row=nomination,
        user_ids=user_ids,
        category_id=1,
        generation_run_id="test-run",
        corpus_sha256="test-hash",
        seed=20260921,
    )
    metadata = json.loads(values[18])

    assert metadata["schema_version"] == 3
    assert metadata["pattern_taxonomy_version"] == "gnn-v3-patterns-v1"
    assert metadata["directory_seed"] == 20260912
    assert metadata["scenario_id"] == nomination.scenario_id
    assert metadata["scenario_phase"] == nomination.scenario_phase
    assert metadata["context_mode"] == nomination.context_mode
    assert metadata["ground_truth"] == nomination.training_disposition
    assert metadata["confirmed_patterns"] == list(nomination.confirmed_patterns)
