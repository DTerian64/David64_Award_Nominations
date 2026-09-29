"""Contract tests for the coordinated integrity-schema migration."""

import importlib.util
from pathlib import Path
from unittest.mock import patch


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0072_integrity_schema.py"
)
spec = importlib.util.spec_from_file_location("migration_0072", MIGRATION_PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


EXPECTED_TABLES = {
    "GNN_UserEmbeddings",
    "GNNScoringPolicies",
    "GraphPatternFindings",
    "GraphScoringChangeRequests",
    "GraphScoringPatternParameters",
    "GraphScoringPolicies",
    "IntegrityComponentStatus",
    "IntegrityComponentStatus_History",
    "IntegrityDecisionResults",
    "NomGraph_NominationEmbedding",
    "NomGraph_Nominated",
    "NomGraph_Person",
    "UserGraphFlags",
    "ApproverPairFlags",
}


def _capture(operation):
    executed = []
    with patch.object(migration.op, "execute", side_effect=executed.append):
        operation()
    return executed, "\n".join(str(statement) for statement in executed)


def test_revision_follows_data_as_of_migration():
    assert migration.revision == "0072"
    assert migration.down_revision == "0071"
    assert set(migration._TABLES) == EXPECTED_TABLES


def test_upgrade_creates_schema_and_transfers_every_table():
    executed, ddl = _capture(migration.upgrade)

    assert "CREATE SCHEMA [integrity] AUTHORIZATION [dbo]" in ddl
    for table in EXPECTED_TABLES:
        assert (
            f"ALTER SCHEMA [integrity] TRANSFER [dbo].[{table}]" in ddl
        )

    assert ddl.index("SYSTEM_VERSIONING = OFF") < ddl.index(
        "TRANSFER [dbo].[IntegrityComponentStatus_History]"
    )
    assert ddl.index("TRANSFER [dbo].[IntegrityComponentStatus_History]") < ddl.index(
        "SYSTEM_VERSIONING = ON"
    )
    assert "HISTORY_RETENTION_PERIOD = 24 MONTHS" in ddl
    assert "DATA_CONSISTENCY_CHECK = ON" in ddl
    assert "is_node = 1" in ddl
    assert "is_edge = 1" in ddl
    assert "sys.foreign_keys" in ddl
    assert "is_not_trusted = 1" in ddl
    assert len(executed) == len(EXPECTED_TABLES) + 5


def test_preflight_rejects_unsafe_permissions_dependencies_and_identity():
    _, ddl = _capture(migration.upgrade)

    assert "sys.database_permissions" in ddl
    assert "sys.sql_expression_dependencies" in ddl
    assert "HAS_PERMS_BY_NAME" in ddl
    assert "direct object permissions" in ddl
    assert "database module references" in ddl
    assert "CONTROL on every source table" in ddl


def test_downgrade_moves_objects_back_and_removes_empty_schema():
    _, ddl = _capture(migration.downgrade)

    for table in EXPECTED_TABLES:
        assert (
            f"ALTER SCHEMA [dbo] TRANSFER [integrity].[{table}]" in ddl
        )
    assert "DROP SCHEMA [integrity]" in ddl
    assert "HISTORY_TABLE = [dbo].[IntegrityComponentStatus_History]" in ddl
