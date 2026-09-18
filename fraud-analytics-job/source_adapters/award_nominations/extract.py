"""SQL extraction owned exclusively by the Award Nomination adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from source_adapters.contracts import SourceReadRequest

from .source_policy import DROPPED_SOURCE_STATUSES


def _database_datetime(value: datetime) -> datetime:
    """Convert an aware UTC boundary to SQL Server's naive DATETIME2 form."""

    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _fetch_dicts(cursor: Any) -> list[dict[str, Any]]:
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def fetch_tenant(connection: Any, tenant_id: int) -> dict[str, Any]:
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT t.TenantId, t.TenantName, CAST(t.is_synthetic AS INT) AS IsSynthetic
        FROM dbo.Tenants t
        WHERE t.TenantId = ?
        """,
        tenant_id,
    )
    rows = _fetch_dicts(cursor)
    if not rows:
        raise ValueError(f"Tenant {tenant_id} does not exist")
    if len(rows) != 1:
        raise ValueError(f"Tenant lookup returned {len(rows)} rows for {tenant_id}")
    return rows[0]


def fetch_users(connection: Any, tenant_id: int) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT u.UserId, u.TenantId, u.Title,
               u.created_at AS CreatedAt, u.updated_at AS UpdatedAt
        FROM dbo.Users u
        WHERE u.TenantId = ?
        ORDER BY u.UserId
        """,
        tenant_id,
    )
    return _fetch_dicts(cursor)


def fetch_nominations(
    connection: Any,
    request: SourceReadRequest,
) -> list[dict[str, Any]]:
    window_clause = ""
    dropped_statuses = sorted(DROPPED_SOURCE_STATUSES)
    if dropped_statuses != ["DO_NOT_USE"]:
        raise RuntimeError("Award Nomination SQL must be updated for source status policy")
    parameters: list[Any] = [
        request.tenant_id,
        _database_datetime(request.as_of_exclusive),
        dropped_statuses[0],
    ]
    if request.window_start_inclusive is not None:
        window_clause = "AND n.NominationDate >= ?"
        parameters.append(_database_datetime(request.window_start_inclusive))

    cursor = connection.cursor()
    cursor.execute(
        f"""
        SELECT
            n.NominationId,
            n.NominatorId,
            n.BeneficiaryId,
            n.ApproverId,
            n.Amount,
            n.Currency,
            n.NominationDescription,
            n.NominationDate,
            n.Status,
            n.CategoryId,
            n.RejectionActor,
            n.ApprovedDate,
            n.PayedDate,
            n.created_at AS NominationCreatedAt,
            n.updated_at AS NominationUpdatedAt,
            nominator.TenantId AS NominatorTenantId,
            beneficiary.TenantId AS BeneficiaryTenantId,
            approver.TenantId AS ApproverTenantId,
            decision.TenantId AS DecisionTenantId,
            decision.TrainingDisposition,
            decision.TrainingDispositionSource,
            decision.TrainingDispositionMetadataJson,
            decision.ReviewedBy,
            decision.ReviewedAt,
            decision.CreatedAt AS DecisionCreatedAt,
            decision.UpdatedAt AS DecisionUpdatedAt
        FROM dbo.Nominations n
        JOIN dbo.Users nominator
          ON nominator.UserId = n.NominatorId
        LEFT JOIN dbo.Users beneficiary
          ON beneficiary.UserId = n.BeneficiaryId
        LEFT JOIN dbo.Users approver
          ON approver.UserId = n.ApproverId
        LEFT JOIN dbo.IntegrityDecisionResults decision
          ON decision.NominationId = n.NominationId
        WHERE nominator.TenantId = ?
          AND n.NominationDate < ?
          AND (n.Status IS NULL OR n.Status <> ?)
          {window_clause}
        ORDER BY n.NominationDate, n.NominationId
        """,
        *parameters,
    )
    return _fetch_dicts(cursor)
