"""Durable producer-owned status for RF, Graph Analytics, and GNN."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


_COMPONENTS = {"RF", "GRAPH", "GNN"}
_ATTEMPT_STATUSES = {"SUCCEEDED", "SKIPPED", "FAILED", "DISABLED"}
_SERVING_STATUSES = {"UNKNOWN", "AVAILABLE", "UNAVAILABLE", "STALE"}
_ACTOR = "svc:fraud-analytics-job"


def upsert_component_status(
    conn,
    *,
    tenant_id: int,
    component: str,
    attempt_status: str,
    reason_code: str | None = None,
    reason_detail: str | None = None,
    diagnostics: dict[str, Any] | None = None,
    run_id: str | None = None,
    serving_status: str | None = None,
    serving_version: str | None = None,
    serving_as_of: object | None = None,
    attempted_at: datetime | None = None,
) -> None:
    """Upsert one producer attempt while preserving an older serving artifact.

    ``serving_status=None`` means "leave the current serving state unchanged".
    On the first skipped/failed attempt there is no prior state, so the inserted
    row correctly starts as UNAVAILABLE.
    """
    component = component.upper()
    attempt_status = attempt_status.upper()
    if component not in _COMPONENTS:
        raise ValueError(f"Unsupported integrity component: {component}")
    if attempt_status not in _ATTEMPT_STATUSES:
        raise ValueError(f"Unsupported attempt status: {attempt_status}")
    if serving_status is not None:
        serving_status = serving_status.upper()
        if serving_status not in _SERVING_STATUSES:
            raise ValueError(f"Unsupported serving status: {serving_status}")

    attempt_time = attempted_at or datetime.now(timezone.utc)
    # Azure SQL DATETIME2 is UTC-by-convention but timezone-naive on the wire.
    # Normalise aware values before pyodbc binds them.
    if attempt_time.tzinfo is not None:
        attempt_time = attempt_time.astimezone(timezone.utc).replace(tzinfo=None)
    last_success = attempt_time if attempt_status == "SUCCEEDED" else None
    diagnostics_json = (
        json.dumps(diagnostics, separators=(",", ":"), default=str)
        if diagnostics is not None else None
    )
    reason_detail = reason_detail[:1000] if reason_detail else None

    cur = conn.cursor()
    cur.execute("""
        MERGE dbo.IntegrityComponentStatus AS target
        USING (SELECT ? AS TenantId, ? AS Component) AS source
            ON  target.TenantId = source.TenantId
            AND target.Component = source.Component
        WHEN MATCHED THEN UPDATE SET
            ServingStatus = COALESCE(?, target.ServingStatus),
            ServingVersion = COALESCE(?, target.ServingVersion),
            ServingAsOf = COALESCE(?, target.ServingAsOf),
            LastAttemptStatus = ?,
            ReasonCode = ?,
            ReasonDetail = ?,
            DiagnosticsJson = ?,
            LastAttemptAt = ?,
            LastSuccessfulAt = COALESCE(?, target.LastSuccessfulAt),
            RunId = ?,
            UpdatedAt = SYSUTCDATETIME(),
            UpdatedBy = ?
        WHEN NOT MATCHED THEN INSERT (
            TenantId, Component, ServingStatus, ServingVersion, ServingAsOf,
            LastAttemptStatus, ReasonCode, ReasonDetail, DiagnosticsJson,
            LastAttemptAt, LastSuccessfulAt, RunId, CreatedBy, UpdatedBy
        ) VALUES (
            ?, ?, COALESCE(?, 'UNAVAILABLE'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        );
    """, (
        tenant_id, component,
        serving_status, serving_version, serving_as_of,
        attempt_status, reason_code, reason_detail, diagnostics_json,
        attempt_time, last_success, run_id, _ACTOR,
        tenant_id, component, serving_status, serving_version, serving_as_of,
        attempt_status, reason_code, reason_detail, diagnostics_json,
        attempt_time, last_success, run_id, _ACTOR, _ACTOR,
    ))
    conn.commit()
