"""Contract tests for the run-level analytics data cutoff migration."""

import importlib.util
from pathlib import Path
from unittest.mock import patch


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0071_integrity_analytics_data_as_of.py"
)
spec = importlib.util.spec_from_file_location("migration_0071", MIGRATION_PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_migration_follows_coordination_schema():
    assert migration.revision == "0071"
    assert migration.down_revision == "0070"


def test_upgrade_adds_backfills_and_defaults_shared_cutoff():
    executed = []
    with patch.object(migration.op, "execute", side_effect=executed.append):
        migration.upgrade()

    ddl = "\n".join(executed)
    assert "DataAsOfUtc DATETIME2(3) NULL" in ddl
    assert "SET DataAsOfUtc = StartedAt" in ddl
    assert "ALTER COLUMN DataAsOfUtc DATETIME2(3) NOT NULL" in ddl
    assert "DEFAULT SYSUTCDATETIME() FOR DataAsOfUtc" in ddl
    assert len(executed) == 4
    assert "ADD DataAsOfUtc" in executed[0]
    assert "SET DataAsOfUtc = StartedAt" in executed[1]
    assert "ALTER COLUMN DataAsOfUtc" in executed[2]
    assert "DEFAULT SYSUTCDATETIME() FOR DataAsOfUtc" in executed[3]


def test_downgrade_removes_default_before_column():
    executed = []
    with patch.object(migration.op, "execute", side_effect=executed.append):
        migration.downgrade()

    ddl = "\n".join(executed)
    assert len(executed) == 2
    assert ddl.index("DROP CONSTRAINT") < ddl.index("DROP COLUMN DataAsOfUtc")
