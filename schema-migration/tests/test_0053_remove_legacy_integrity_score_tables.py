"""Safety checks for the irreversible legacy score-table cleanup."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0053_remove_legacy_integrity_score_tables.py"
)
SPEC = importlib.util.spec_from_file_location("migration_0053", MIGRATION_PATH)
MIGRATION = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MIGRATION)


EXPECTED_TABLES = (
    "HRBP_FraudFlags",
    "Graph_FraudScores",
    "GNN_FraudScores",
    "Appr_FraudScores",
    "P2P_FraudScores",
    "FraudDecisionResults",
)


def test_upgrade_drops_all_six_tables_after_preflight(monkeypatch):
    drop_table = MagicMock()
    monkeypatch.setattr(MIGRATION, "_table_exists", lambda _name: True)
    monkeypatch.setattr(MIGRATION, "_unmapped_count", lambda _name: 0)
    monkeypatch.setattr(MIGRATION, "_database_dependents", lambda _name: [])
    monkeypatch.setattr(MIGRATION.op, "drop_table", drop_table)

    MIGRATION.upgrade()

    assert MIGRATION._LEGACY_TABLES == EXPECTED_TABLES
    assert [call.args[0] for call in drop_table.call_args_list] == list(EXPECTED_TABLES)
    assert all(call.kwargs == {"schema": "dbo"} for call in drop_table.call_args_list)


def test_upgrade_refuses_to_drop_unmapped_approver_scores(monkeypatch):
    drop_table = MagicMock()
    monkeypatch.setattr(MIGRATION, "_table_exists", lambda _name: True)
    monkeypatch.setattr(
        MIGRATION,
        "_unmapped_count",
        lambda name: 1 if name == "Appr_FraudScores" else 0,
    )
    monkeypatch.setattr(MIGRATION, "_database_dependents", lambda _name: [])
    monkeypatch.setattr(MIGRATION.op, "drop_table", drop_table)

    with pytest.raises(RuntimeError, match="dbo.Appr_FraudScores"):
        MIGRATION.upgrade()

    drop_table.assert_not_called()


def test_downgrade_is_explicitly_irreversible():
    with pytest.raises(RuntimeError, match="intentionally irreversible"):
        MIGRATION.downgrade()
