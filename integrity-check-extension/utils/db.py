"""Tenant-safe SQL claims and score-reproduction inputs."""

from __future__ import annotations

import json
import os
import struct
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pyodbc

from extensions.gnn_explainer.contracts import ExplanationRequest
from extensions.gnn_explainer.errors import PermanentExtensionError
from .azure_credential import credential

_AUDIT_ACTOR = "svc:integrity-check-extension"
_DRIVER = os.getenv("DB_DRIVER", "{ODBC Driver 18 for SQL Server}")
_SQL_COPT_SS_ACCESS_TOKEN = 1256
_AZURE_SQL_SCOPE = "https://database.windows.net/.default"


@contextmanager
def _get_conn():
    connection_string = (
        f"Driver={_DRIVER};Server={os.environ['SQL_SERVER']};"
        f"Database={os.environ['SQL_DATABASE']};"
        "Encrypt=yes;TrustServerCertificate=no;"
    )
    token = credential.get_token(_AZURE_SQL_SCOPE).token.encode("utf-16-le")
    packed = struct.pack(f"<I{len(token)}s", len(token), token)
    connection = pyodbc.connect(
        connection_string,
        attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: packed},
    )
    try:
        yield connection
    finally:
        connection.close()


@dataclass(frozen=True)
class RequestContext:
    details: dict
    gnn_result: dict
    policy_configuration: dict


def load_request_context(request: ExplanationRequest) -> RequestContext:
    """Load the canonical decision only through matching nomination ownership."""
    with _get_conn() as connection:
        cursor = connection.cursor()
        cursor.execute("""
            SELECT n.Amount, n.CategoryId, n.NominationDate,
                   n.NominatorId, n.BeneficiaryId,
                   decision.GnnResultJson, policy.ConfigurationJson,
                   policy.ExplanationEnabled
            FROM dbo.Nominations n
            JOIN dbo.Users owner ON owner.UserId = n.NominatorId
            JOIN dbo.IntegrityDecisionResults decision
              ON decision.NominationId = n.NominationId
             AND decision.TenantId = owner.TenantId
            JOIN dbo.GNNScoringPolicies policy
              ON policy.TenantId = owner.TenantId AND policy.Status = 'ACTIVE'
            WHERE n.NominationId = ? AND owner.TenantId = ?
        """, request.nomination_id, request.tenant_id)
        row = cursor.fetchone()
    if row is None:
        raise PermanentExtensionError("CANONICAL_DECISION_NOT_FOUND")
    try:
        gnn_result = json.loads(row[5])
        configuration = json.loads(row[6])
    except (TypeError, json.JSONDecodeError) as exc:
        raise PermanentExtensionError("INVALID_CANONICAL_JSON") from exc
    if gnn_result.get("model_version") != request.model_version:
        raise PermanentExtensionError("DECISION_MODEL_MISMATCH")
    if gnn_result.get("graph_snapshot_id") != request.graph_snapshot_id:
        raise PermanentExtensionError("DECISION_SNAPSHOT_MISMATCH")
    explanation = gnn_result.get("explanation") or {}
    if explanation.get("request_id") != request.request_id:
        raise PermanentExtensionError("DECISION_REQUEST_MISMATCH")
    if not bool(row[7]):
        raise PermanentExtensionError("EXPLANATION_DISABLED_BY_POLICY")
    return RequestContext(
        details={
            "nomination_id": request.nomination_id,
            "amount": float(row[0]),
            "category_id": row[1],
            "nomination_date": row[2],
            "nominator_id": int(row[3]),
            "beneficiary_id": int(row[4]),
        },
        gnn_result=gnn_result,
        policy_configuration=configuration if isinstance(configuration, dict) else {},
    )


def claim_request(request: ExplanationRequest, delivery_count: int) -> bool:
    """Atomically acquire only the matching request; false means already complete/busy."""
    running = json.dumps({
        "method": "GNNEXPLAINER",
        "status": "RUNNING",
        "request_id": request.request_id,
        "requested_at": request.requested_at,
        "last_attempt_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "attempt": int(delivery_count),
    }, separators=(",", ":"))
    with _get_conn() as connection:
        cursor = connection.cursor()
        cursor.execute("""
            UPDATE dbo.IntegrityDecisionResults
            SET GnnResultJson = JSON_MODIFY(
                    GnnResultJson, '$.explanation', JSON_QUERY(?)
                ),
                UpdatedAt = SYSUTCDATETIME()
            WHERE NominationId = ? AND TenantId = ?
              AND JSON_VALUE(GnnResultJson, '$.model_version') = ?
              AND JSON_VALUE(GnnResultJson, '$.graph_snapshot_id') = ?
              AND JSON_VALUE(GnnResultJson, '$.explanation.request_id') = ?
              AND (
                    JSON_VALUE(GnnResultJson, '$.explanation.status') = 'REQUESTED'
                 OR JSON_VALUE(GnnResultJson, '$.explanation.status') = 'FAILED'
                 OR (
                    JSON_VALUE(GnnResultJson, '$.explanation.status') = 'RUNNING'
                    AND COALESCE(
                        TRY_CONVERT(datetime2, JSON_VALUE(
                            GnnResultJson, '$.explanation.last_attempt_at'
                        ), 127), CONVERT(datetime2, '19000101', 112)
                    ) < DATEADD(minute, -14, SYSUTCDATETIME())
                 )
              )
        """, running, request.nomination_id, request.tenant_id,
             request.model_version, request.graph_snapshot_id, request.request_id)
        changed = cursor.rowcount == 1
        connection.commit()
        return changed


def get_versioned_embeddings(
    request: ExplanationRequest, user_ids: list[int]
) -> dict[int, np.ndarray]:
    placeholders = ",".join("?" for _ in user_ids)
    with _get_conn() as connection:
        cursor = connection.cursor()
        cursor.execute(f"""
            WITH ranked AS (
                SELECT UserId, Embedding,
                       ROW_NUMBER() OVER (
                           PARTITION BY UserId
                           ORDER BY AsOfDate DESC, LastUpdatedUtc DESC
                       ) AS row_rank
                FROM dbo.GNN_UserEmbeddings
                WHERE TenantId = ? AND ModelVersion = ?
                  AND UserId IN ({placeholders})
            )
            SELECT UserId, Embedding FROM ranked WHERE row_rank = 1
        """, request.tenant_id, request.model_version, *user_ids)
        return {
            int(user_id): np.frombuffer(bytes(blob), dtype=np.float32)
            for user_id, blob in cursor.fetchall()
        }


def finish_request(request: ExplanationRequest, explanation: dict) -> bool:
    """Conditionally replace only this worker's RUNNING lifecycle object."""
    serialized = json.dumps(explanation, default=str, separators=(",", ":"))
    with _get_conn() as connection:
        cursor = connection.cursor()
        cursor.execute("""
            UPDATE dbo.IntegrityDecisionResults
            SET GnnResultJson = JSON_MODIFY(
                    GnnResultJson, '$.explanation', JSON_QUERY(?)
                ),
                UpdatedAt = SYSUTCDATETIME()
            WHERE NominationId = ? AND TenantId = ?
              AND JSON_VALUE(GnnResultJson, '$.model_version') = ?
              AND JSON_VALUE(GnnResultJson, '$.graph_snapshot_id') = ?
              AND JSON_VALUE(GnnResultJson, '$.explanation.request_id') = ?
              AND JSON_VALUE(GnnResultJson, '$.explanation.status') = 'RUNNING'
        """, serialized, request.nomination_id, request.tenant_id,
             request.model_version, request.graph_snapshot_id, request.request_id)
        changed = cursor.rowcount == 1
        connection.commit()
        return changed


def insert_nomination_log(
    request: ExplanationRequest,
    level: str,
    message: str,
    details: dict,
    exception: str | None = None,
) -> None:
    with _get_conn() as connection:
        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO dbo.Nomination_Logs (
                nomination_id, tenant_id, log_time, level, service, logger,
                message, message_id, details, exception, created_by, updated_by
            ) VALUES (?, ?, SYSUTCDATETIME(), ?, 'integrity-check-extension',
                      'integrity_check_extension.gnn_explainer', ?, ?, ?, ?, ?, ?)
        """, request.nomination_id, request.tenant_id, level, message,
             request.request_id, json.dumps(details, default=str), exception,
             _AUDIT_ACTOR, _AUDIT_ACTOR)
        connection.commit()
