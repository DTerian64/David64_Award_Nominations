"""Shared model publication and tenant configuration tests."""

from __future__ import annotations

import json

import pandas as pd

from utils import model_artifacts, tenant_model_config


class _Cursor:
    def __init__(self, value):
        self.value = value
        self.executed = None

    def execute(self, statement, parameters):
        self.executed = (statement, parameters)

    def fetchone(self):
        return self.value


class _Connection:
    def __init__(self, value=None):
        self._cursor = _Cursor(value)
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def test_get_tenants_preserves_database_order(monkeypatch):
    frame = pd.DataFrame(
        [(1, "Demo"), (5, "Synthetics Inc")],
        columns=["TenantId", "TenantName"],
    )
    monkeypatch.setattr(tenant_model_config.pd, "read_sql", lambda *_: frame)

    assert tenant_model_config.get_tenants(object()) == [
        (1, "Demo"),
        (5, "Synthetics Inc"),
    ]


def test_tenant_embed_model_reads_json_and_closes_connection(monkeypatch):
    connection = _Connection(
        (json.dumps({"embed_model": "tenant-embedding-model"}),)
    )
    monkeypatch.setattr(tenant_model_config, "connect", lambda: connection)

    assert (
        tenant_model_config.get_tenant_embed_model(5)
        == "tenant-embedding-model"
    )
    assert connection.closed is True


def test_tenant_embed_model_falls_back_for_malformed_json(monkeypatch):
    connection = _Connection(("not-json",))
    monkeypatch.setattr(tenant_model_config, "connect", lambda: connection)

    assert (
        tenant_model_config.get_tenant_embed_model(5)
        == tenant_model_config.DEFAULT_EMBED_MODEL_NAME
    )


def test_upload_without_storage_account_is_explicitly_skipped(
    tmp_path, monkeypatch
):
    artifact = tmp_path / "model.pkl"
    artifact.write_bytes(b"model")
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT", raising=False)

    assert (
        model_artifacts.upload_artifact(
            artifact,
            blob_folder="tenant_5/tabular/version",
        )
        is False
    )
