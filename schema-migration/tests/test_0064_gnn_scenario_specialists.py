"""GNN scenario-specialist policy migration contract."""

import importlib.util
import json
from pathlib import Path


def _path() -> Path:
    return Path(__file__).parents[1] / "alembic/versions/0064_gnn_scenario_specialists.py"


def _load():
    spec = importlib.util.spec_from_file_location("migration_0064", _path())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_and_direct_serving_mode():
    migration = _load()
    assert migration.revision == "0064"
    assert migration.down_revision == "0063"
    configuration = json.loads(migration._v3_configuration(json.dumps({
        "schema_version": 1,
        "model": {},
        "training": {},
        "artifacts": {},
        "architecture_selection": {},
        "score_routing": {},
    })))
    assert configuration["serving_mode"] == "scenario_specialists"
    assert "scenario_specialists_shadow" not in _path().read_text()
    assert set(configuration["behavior_tracks"]) == set(migration._TRACKS)


def test_migration_targets_only_synthetic_tenants():
    source = _path().read_text(encoding="utf-8")
    assert "t.is_synthetic=1" in source
    assert "Status='RETIRED'" in source
