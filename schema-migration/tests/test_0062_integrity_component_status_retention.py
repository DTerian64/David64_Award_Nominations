"""Static contract checks for migration 0062."""

from pathlib import Path


MIGRATION = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0062_integrity_component_status_retention.py"
)


def test_upgrade_sets_two_year_temporal_history_retention():
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "0062"' in source
    assert 'down_revision = "0061"' in source
    assert "ALTER TABLE dbo.IntegrityComponentStatus" in source
    assert "HISTORY_RETENTION_PERIOD = 24 MONTHS" in source
    assert "temporal_type" in source


def test_downgrade_restores_infinite_retention():
    source = MIGRATION.read_text(encoding="utf-8")

    assert "HISTORY_RETENTION_PERIOD = INFINITE" in source
