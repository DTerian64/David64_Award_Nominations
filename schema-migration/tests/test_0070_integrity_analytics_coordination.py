"""Contract tests for the integrity analytics coordination migration."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0070_integrity_analytics_coordination.py"
)
spec = importlib.util.spec_from_file_location("migration_0070", MIGRATION_PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_migration_is_next_revision_and_creates_ops_schema():
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert migration.revision == "0070"
    assert migration.down_revision == "0069"
    assert "CREATE SCHEMA ops AUTHORIZATION dbo" in source


def test_three_integrity_tables_have_relational_coordination_constraints():
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "CREATE TABLE ops.IntegrityAnalyticsJobRuns" in source
    assert "CREATE TABLE ops.IntegrityAnalyticsTenantRuns" in source
    assert "CREATE TABLE ops.IntegrityAnalyticsStageAttempts" in source
    assert "UQ_IntegrityAnalyticsJobRuns_ExecutionName" in source
    assert "PRIMARY KEY CLUSTERED (RunId, TenantId)" in source
    assert "UNIQUE (RunId, TenantId, Stage, AttemptNumber)" in source
    assert "IX_IntegrityAnalyticsTenantRuns_Claim" in source
    assert "IX_IntegrityAnalyticsStageAttempts_Operations" in source


def test_json_is_bounded_diagnostic_data_only():
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "SummaryJson                 NVARCHAR(4000)" in source
    assert "DiagnosticsJson NVARCHAR(4000)" in source
    assert "ISJSON(SummaryJson)" in source
    assert "ISJSON(DiagnosticsJson)" in source
    assert "LeaseExpiresAt" in source
    assert "PreparationLeaseExpiresAt" in source
    assert "FinalizationLeaseExpiresAt" in source


def test_upgrade_is_guarded_and_downgrade_drops_in_dependency_order():
    executed = []

    with (
        patch.object(migration, "_table_exists", return_value=False),
        patch.object(migration.op, "execute", side_effect=executed.append),
    ):
        migration.upgrade()

    ddl = "\n".join(executed)
    assert ddl.count("CREATE TABLE ops.IntegrityAnalytics") == 3

    with (
        patch.object(migration, "_table_exists", return_value=True),
        patch.object(migration.op, "execute", MagicMock()) as execute,
    ):
        migration.downgrade()

    statements = [call.args[0] for call in execute.call_args_list]
    assert "StageAttempts" in statements[0]
    assert "TenantRuns" in statements[1]
    assert "JobRuns" in statements[2]
    assert "DROP SCHEMA ops" in statements[3]

