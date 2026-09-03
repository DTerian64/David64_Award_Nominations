"""Static contract tests for the expanded Graph detector catalogue."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0052_expand_graph_detectors.py"
)
SPEC = importlib.util.spec_from_file_location("migration_0052", MIGRATION_PATH)
MIGRATION = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MIGRATION)


def test_detector_order_is_unique_contiguous_and_importance_ranked():
    assert MIGRATION._ORDER == {
        "Ring": 1,
        "BipartiteDenseBlock": 2,
        "TemporalBurst": 3,
        "SuperNominator": 4,
        "SuperBeneficiary": 5,
        "CopyPaste": 6,
        "HiddenCandidate": 7,
        "Desert": 8,
    }


def test_new_detector_defaults_preserve_monitor_only_super_beneficiary():
    patterns = MIGRATION._NEW_PATTERNS

    assert patterns["BipartiteDenseBlock"]["routing"] is True
    assert patterns["TemporalBurst"]["routing"] is True
    assert patterns["SuperBeneficiary"]["routing"] is False
    assert patterns["SuperBeneficiary"]["roles"] == ["beneficiary"]
    for pattern in patterns.values():
        weighted_total = sum(
            value
            for key, value in pattern["parameters"].items()
            if key.endswith("_weight")
        )
        assert pattern["base"] + weighted_total <= 100
