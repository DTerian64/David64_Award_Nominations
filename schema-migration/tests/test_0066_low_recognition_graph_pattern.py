"""Contract for the analytics-only v5 participation pattern."""

import importlib.util
from pathlib import Path


PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "0066_low_recognition_graph_pattern.py"
)
SPEC = importlib.util.spec_from_file_location("migration_0066", PATH)
MIGRATION = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MIGRATION)


def test_default_is_analytics_only_and_has_8_4_1_eligibility():
    assert MIGRATION.revision == "0066"
    assert MIGRATION.down_revision == "0065"
    assert MIGRATION.PATTERN == "LowRecognitionNominator"
    assert MIGRATION.PARAMETERS["minimum_nominations_made"] == 8
    assert MIGRATION.PARAMETERS["minimum_distinct_beneficiaries"] == 4
    assert MIGRATION.PARAMETERS["maximum_nominations_received"] == 1
    assert (
        10 + MIGRATION.PARAMETERS["activity_weight"]
        + MIGRATION.PARAMETERS["breadth_weight"]
    ) <= 100


def test_pattern_is_enabled_and_analytics_only_for_all_policies():
    source = PATH.read_text(encoding="utf-8")
    assert ":policy_id, :pattern, 9, 1, 0" in source
    assert "_enabled_for_tenant" not in source
