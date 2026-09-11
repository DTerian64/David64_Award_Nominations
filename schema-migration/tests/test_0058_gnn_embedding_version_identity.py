"""GNN embedding identity migration contract."""

import importlib.util
from pathlib import Path


def _load_migration():
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/0058_gnn_embedding_version_identity.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0058", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_revision_follows_current_head():
    migration = _load_migration()
    assert migration.revision == "0058"
    assert migration.down_revision == "0057"


def test_upgrade_key_contains_model_version():
    source = (
        Path(__file__).parents[1]
        / "alembic/versions/0058_gnn_embedding_version_identity.py"
    ).read_text(encoding="utf-8")
    assert "PRIMARY KEY CLUSTERED (TenantId, UserId, ModelVersion, AsOfDate)" in source
    assert "PARTITION BY TenantId, UserId, AsOfDate" in source
