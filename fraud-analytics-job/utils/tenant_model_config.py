"""Tenant discovery and tenant-scoped model configuration."""

from __future__ import annotations

import json
import logging

import pandas as pd

from utils.db_conn import connect


DEFAULT_EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
INTEGRITY_ANALYTICS_JOB_CONFIG_KEY = "integrity_analytics_job"
_LEGACY_FRAUD_ANALYTICS_JOB_CONFIG_KEY = "fraud_analytics_job"

logger = logging.getLogger(__name__)


def is_integrity_analytics_enabled(
    raw_configuration: str | None,
    tenant_id: int | None = None,
) -> bool:
    """Return tenant job eligibility, defaulting safely to enabled.

    Existing tenants have no switch yet. Missing, malformed, non-object, and
    non-boolean values therefore remain enabled rather than silently removing a
    tenant from scheduled processing.
    """
    if not raw_configuration:
        return True
    try:
        configuration = json.loads(raw_configuration)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "Tenant %s has malformed integrity_config; scheduled integrity "
            "analytics remains enabled by default",
            tenant_id if tenant_id is not None else "unknown",
        )
        return True
    if not isinstance(configuration, dict):
        return True
    job = configuration.get(INTEGRITY_ANALYTICS_JOB_CONFIG_KEY)
    if not isinstance(job, dict):
        job = configuration.get(_LEGACY_FRAUD_ANALYTICS_JOB_CONFIG_KEY)
    if not isinstance(job, dict):
        return True
    enabled = job.get("enabled", True)
    return enabled if isinstance(enabled, bool) else True


def get_tenant_tabular_window(connection, tenant_id: int) -> int:
    cursor = connection.cursor()
    cursor.execute("""
        SELECT TRY_CONVERT(int, JSON_VALUE(CAST(integrity_config AS nvarchar(max)),
            '$.tabular.window_days')) FROM dbo.Tenants WHERE TenantId = ?
    """, tenant_id)
    row = cursor.fetchone()
    days = int(row[0]) if row and row[0] is not None else 365
    if days < 1:
        raise ValueError("Tabular window_days must be positive")
    return days


def get_tenants(connection) -> list[tuple[int, str]]:
    """Return enabled tenant identifiers and names in deterministic order."""
    frame = pd.read_sql(
        "SELECT TenantId, TenantName, integrity_config FROM dbo.Tenants ORDER BY TenantId",
        connection,
    )
    return [
        (int(tenant_id), str(tenant_name))
        for tenant_id, tenant_name, configuration in frame.itertuples(index=False, name=None)
        if is_integrity_analytics_enabled(configuration, int(tenant_id))
    ]


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
