"""Static contract checks for migration 0061."""

from pathlib import Path


MIGRATION = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0061_align_synthetic_rejected_routes.py"
)


def test_upgrade_repairs_only_rejected_synthetic_fraud_routes():
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "0061"' in source
    assert 'down_revision = "0060"' in source
    assert "tenant.is_synthetic = 1" in source
    assert "nomination.Status = 'Rejected'" in source
    assert "TrainingDisposition = 'FRAUD'" in source
    assert "TrainingDispositionSource =" in source
    assert "'SYNTHETIC_GROUND_TRUTH'" in source
    assert "SET FinalRoute = 'HRBP_REVIEW'" in source
    assert "ReviewScope = 'FRAUD'" in source


def test_downgrade_restores_original_import_route():
    source = MIGRATION.read_text(encoding="utf-8")

    assert "SET FinalRoute = 'MANAGER_APPROVAL'" in source
    assert "ReviewScope = NULL" in source
