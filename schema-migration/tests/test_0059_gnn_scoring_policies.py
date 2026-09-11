"""GNN scoring policy migration contract."""

import importlib.util
from pathlib import Path


def _path() -> Path:
    return (
        Path(__file__).parents[1]
        / "alembic/versions/0059_gnn_scoring_policies.py"
    )


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0059", _path())
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_follows_current_head():
    migration = _load_migration()
    assert migration.revision == "0059"
    assert migration.down_revision == "0058"


def test_policy_owns_training_serving_and_explanation_settings():
    source = _path().read_text(encoding="utf-8")
    for column in (
        "TrainingEnabled", "InferenceEnabled", "ConfigurationJson",
        "ExplanationEnabled", "ExplanationMinimumRisk",
    ):
        assert column in source
    for setting in (
        '"schema_version": 1', '"model"', '"training"', '"artifacts"',
        '"architecture_selection"', '"score_routing"',
        '"candidate_architectures"', '"minimum_improvement_over_mlp"',
        '"critical_threshold"',
    ):
        assert setting in source
    assert "ConfigurationJson NVARCHAR(MAX) NOT NULL" in source
    assert "ISJSON(ConfigurationJson) = 1" in source
    assert "HiddenDimension SMALLINT" not in source
    assert "CandidateArchitecturesJson NVARCHAR" not in source


def test_policy_has_one_active_and_one_draft_per_tenant():
    source = _path().read_text(encoding="utf-8")
    assert "UX_GNNScoringPolicies_Active" in source
    assert "WHERE Status='ACTIVE'" in source
    assert "UX_GNNScoringPolicies_Draft" in source
    assert "WHERE Status='DRAFT'" in source


def test_default_configuration_is_versioned_and_not_double_encoded():
    migration = _load_migration()

    configuration = migration._configuration({"critical_threshold": 91})

    assert configuration["schema_version"] == 1
    assert configuration["architecture_selection"]["candidate_architectures"] == [
        "graphsage", "gcn", "gatv2",
    ]
    assert configuration["score_routing"]["critical_threshold"] == 91
