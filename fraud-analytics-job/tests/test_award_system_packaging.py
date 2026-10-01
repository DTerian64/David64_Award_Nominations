"""Contracts for the Award Nomination vertical package."""

import importlib
from pathlib import Path

from systems.award_nominations.pipeline import (
    STANDALONE_STAGES,
    TENANT_STAGES,
)


def test_production_stages_are_registered_from_award_system_package():
    modules = {stage["module"] for stage in TENANT_STAGES + STANDALONE_STAGES}

    assert modules
    assert all(
        module.startswith("systems.award_nominations.") for module in modules
    )


def test_every_registered_stage_imports():
    for stage in TENANT_STAGES + STANDALONE_STAGES:
        importlib.import_module(stage["module"])


def test_legacy_alias_modules_are_removed():
    root = Path(__file__).resolve().parents[1]
    legacy_paths = (
        root / "source_adapters" / "award_nominations",
        root / "feature_builders" / "source_views.py",
        root / "feature_builders" / "tabular" / "award_nomination_tabular_v1.py",
        root / "integrity_sentinel" / "datasets.py",
        root / "integrity_sentinel" / "graph_analytics.py",
        root / "integrity_sentinel" / "train_gnn_model.py",
        root / "misc_jobs" / "sync_holidays.py",
        root / "modeling" / "forecast_models.py",
        root / "modeling" / "gnn" / "graph.py",
        root / "modeling" / "graph_analytics.py",
        root / "modeling" / "labels.py",
        root / "modeling" / "train_gnn_model.py",
        root / "modeling" / "train_tabular_model.py",
        root / "utils" / "tenant_model_config.py",
    )

    assert [path for path in legacy_paths if path.exists()] == []
