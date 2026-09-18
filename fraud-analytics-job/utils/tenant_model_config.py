"""Tenant discovery and tenant-scoped model configuration."""

from __future__ import annotations

import json

import pandas as pd

from utils.db_conn import connect


DEFAULT_EMBED_MODEL_NAME = "all-MiniLM-L6-v2"


def get_tenants(connection) -> list[tuple[int, str]]:
    """Return all tenant identifiers and names in deterministic order."""
    frame = pd.read_sql(
        "SELECT TenantId, TenantName FROM dbo.Tenants ORDER BY TenantId",
        connection,
    )
    return list(frame.itertuples(index=False, name=None))


def get_tenant_embed_model(tenant_id: int) -> str:
    """Return a tenant's description embedding model, or the safe default."""
    connection = connect()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT desc_check_config FROM dbo.Tenants WHERE TenantId = ?",
            (tenant_id,),
        )
        row = cursor.fetchone()
    finally:
        connection.close()

    if not row or not row[0]:
        return DEFAULT_EMBED_MODEL_NAME
    try:
        config = json.loads(row[0])
        return config.get("embed_model", DEFAULT_EMBED_MODEL_NAME)
    except (json.JSONDecodeError, TypeError):
        print(
            f"[Tenant {tenant_id}] ⚠  Could not parse desc_check_config JSON — "
            f"using default embed model '{DEFAULT_EMBED_MODEL_NAME}'."
        )
        return DEFAULT_EMBED_MODEL_NAME
