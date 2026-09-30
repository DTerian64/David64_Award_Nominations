"""Tenant discovery and tenant-scoped model configuration."""

from __future__ import annotations

import json
import logging

import pandas as pd

from .connection import connect


DEFAULT_EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
INTEGRITY_ANALYTICS_JOB_CONFIG_KEY = "integrity_analytics_job"
_LEGACY_FRAUD_ANALYTICS_JOB_CONFIG_KEY = "fraud_analytics_job"

logger = logging.getLogger(__name__)


def _parse_json_object(
    raw_configuration: str | None,
    *,
    tenant_id: int,
    column: str,
) -> dict:
    if not raw_configuration:
        return {}
    try:
        value = json.loads(raw_configuration)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Tenant %d has malformed %s; using defaults", tenant_id, column)
        return {}
    return value if isinstance(value, dict) else {}


def get_tenant_integrity_config(connection, tenant_id: int) -> dict:
    row = connection.cursor().execute(
        "SELECT integrity_config FROM dbo.Tenants WHERE TenantId = ?",
        (tenant_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Tenant {tenant_id} does not exist")
    return _parse_json_object(
        row[0], tenant_id=tenant_id, column="integrity_config"
    )


def get_tenant_name(connection, tenant_id: int) -> str:
    row = connection.cursor().execute(
        "SELECT TenantName FROM dbo.Tenants WHERE TenantId = ?",
        (tenant_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Tenant {tenant_id} does not exist")
    return str(row[0])


def get_maximum_graph_window(connection, fallback_days: int) -> int:
    """Return the longest configured graph window across source tenants."""

    cursor = connection.cursor()
    cursor.execute("SELECT TenantId, integrity_config FROM dbo.Tenants")
    rows = cursor.fetchall()
    values = [fallback_days]
    for tenant_id, raw_configuration in rows:
        config = _parse_json_object(
            raw_configuration,
            tenant_id=int(tenant_id),
            column="integrity_config",
        )
        graph = config.get("graph_pattern")
        if not isinstance(graph, dict):
            continue
        configured = graph.get("detection_window_days")
        if isinstance(configured, int) and configured > 0:
            values.append(configured)
        detector_windows = graph.get("detector_windows")
        if isinstance(detector_windows, dict):
            values.extend(
                value
                for value in detector_windows.values()
                if isinstance(value, int) and value > 0
            )
    return max(values)


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


def get_tenant_gnn_window(connection, tenant_id: int, fallback_days: int = 365) -> int:
    config = get_tenant_integrity_config(connection, tenant_id)
    gnn = config.get("gnn")
    value = gnn.get("window_days") if isinstance(gnn, dict) else None
    days = value if isinstance(value, int) else fallback_days
    if days < 1:
        raise ValueError("GNN window_days must be positive")
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


def tenant_is_enabled(tenant_id: int) -> bool:
    """Re-read the source-owned tenant switch before starting tenant work."""

    connection = connect()
    try:
        row = connection.cursor().execute(
            "SELECT integrity_config FROM dbo.Tenants WHERE TenantId = ?",
            (tenant_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError(f"Tenant {tenant_id} does not exist")
    return is_integrity_analytics_enabled(row[0], tenant_id)


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
