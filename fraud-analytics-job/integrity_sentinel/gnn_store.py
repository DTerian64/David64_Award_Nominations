"""Integrity Sentinel persistence used by GNN training stages."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

import numpy as np


logger = logging.getLogger(__name__)


def read_status_for_run(
    connection: Any, tenant_id: int, run_id: str
) -> tuple | None:
    return connection.cursor().execute(
        """
        SELECT LastAttemptStatus, ServingVersion, DiagnosticsJson
        FROM integrity.IntegrityComponentStatus
        WHERE TenantId=? AND Component='GNN' AND RunId=?
        """,
        (tenant_id, run_id),
    ).fetchone()


def read_stage_outcome(connection: Any, tenant_id: int) -> tuple | None:
    return connection.cursor().execute(
        """
        SELECT LastAttemptStatus, ReasonCode, ServingVersion
        FROM integrity.IntegrityComponentStatus
        WHERE TenantId=? AND Component='GNN'
        """,
        (tenant_id,),
    ).fetchone()


def publish_user_embeddings(
    connection: Any,
    tenant_id: int,
    user_ids: list[int],
    embeddings: np.ndarray,
    as_of: date,
    model_version: str,
) -> int:
    cursor = connection.cursor()
    cursor.execute(
        """
        CREATE TABLE #gnn_emb (
            UserId INT, AsOfDate DATE, Embedding VARBINARY(MAX),
            EmbeddingDim SMALLINT, ModelVersion VARCHAR(64)
        )
        """
    )
    rows = [
        (
            int(user_id),
            as_of,
            embeddings[index].astype(np.float32).tobytes(),
            int(embeddings.shape[1]),
            model_version,
        )
        for index, user_id in enumerate(user_ids)
    ]
    # Per-row binding avoids pyodbc's default 255-byte buffer for bytes values.
    cursor.fast_executemany = False
    cursor.executemany(
        "INSERT INTO #gnn_emb "
        "(UserId, AsOfDate, Embedding, EmbeddingDim, ModelVersion) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    cursor.execute(
        """
        MERGE integrity.GNN_UserEmbeddings AS target
        USING (SELECT ? AS TenantId, UserId, AsOfDate, Embedding,
                      EmbeddingDim, ModelVersion FROM #gnn_emb) AS src
            ON target.TenantId = src.TenantId
            AND target.UserId = src.UserId
            AND target.ModelVersion = src.ModelVersion
            AND target.AsOfDate = src.AsOfDate
        WHEN MATCHED THEN UPDATE SET
            Embedding = src.Embedding,
            EmbeddingDim = src.EmbeddingDim,
            ModelVersion = src.ModelVersion,
            LastUpdatedUtc = SYSUTCDATETIME()
        WHEN NOT MATCHED THEN INSERT
            (TenantId, UserId, AsOfDate, Embedding, EmbeddingDim, ModelVersion)
        VALUES
            (src.TenantId, src.UserId, src.AsOfDate, src.Embedding,
             src.EmbeddingDim, src.ModelVersion);
        """,
        tenant_id,
    )
    cursor.execute("DROP TABLE #gnn_emb")
    return len(rows)


def evict_stale_user_embeddings(
    connection: Any, tenant_id: int, retention_days: int
) -> int:
    cutoff = date.today() - timedelta(days=retention_days)
    cursor = connection.cursor()
    cursor.execute(
        "DELETE FROM integrity.GNN_UserEmbeddings "
        "WHERE TenantId = ? AND AsOfDate < ?",
        tenant_id,
        cutoff,
    )
    return max(cursor.rowcount, 0)


def load_incumbent_selection(
    connection: Any, tenant_id: int, architectures: tuple | list
) -> dict | None:
    row = connection.cursor().execute(
        """
        SELECT DiagnosticsJson
        FROM integrity.IntegrityComponentStatus
        WHERE TenantId = ? AND Component = 'GNN'
        """,
        tenant_id,
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        selection = (json.loads(row[0]).get("selection") or {})
        return selection if selection.get("selected_architecture") in architectures else None
    except (TypeError, ValueError):
        logger.warning(
            "Tenant %d has invalid GNN selection diagnostics; ignoring incumbent",
            tenant_id,
        )
        return None


def load_incumbent_specialists(connection: Any, tenant_id: int) -> dict[str, dict]:
    row = connection.cursor().execute(
        """
        SELECT ServingVersion, DiagnosticsJson
        FROM integrity.IntegrityComponentStatus
        WHERE TenantId = ? AND Component = 'GNN'
        """,
        tenant_id,
    ).fetchone()
    if not row or not row[1]:
        return {}
    try:
        roster = json.loads(row[1]).get("specialists") or {}
        if not isinstance(roster, dict):
            return {}
        return {
            str(key).upper(): {
                **value,
                "artifact_bundle_version": value.get(
                    "artifact_bundle_version", row[0]
                ),
            }
            for key, value in roster.items()
            if isinstance(value, dict)
            and value.get("state") in {"ACTIVE", "CARRIED_FORWARD"}
        }
    except (TypeError, ValueError):
        logger.warning(
            "Tenant %d has invalid GNN specialist diagnostics; ignoring incumbents",
            tenant_id,
        )
        return {}
