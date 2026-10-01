"""Persist the canonical actor/event topology used by graph exploration tools."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def replace_tenant_graph_projection(
    connection: Any,
    tenant_id: int,
    users: Iterable[Mapping[str, Any]],
    nominations: Iterable[Mapping[str, Any]],
) -> None:
    """Replace one tenant's SQL Graph projection inside the caller transaction."""

    cursor = connection.cursor()
    cursor.execute(
        """
        DELETE edge_row
        FROM integrity.NomGraph_Nominated AS edge_row
        WHERE edge_row.$from_id IN (
            SELECT person.$node_id
            FROM integrity.NomGraph_Person AS person
            WHERE person.TenantId = ?
        )
        """,
        tenant_id,
    )
    cursor.execute(
        "DELETE FROM integrity.NomGraph_Person WHERE TenantId = ?",
        tenant_id,
    )
    for user in users:
        cursor.execute(
            """
            INSERT INTO integrity.NomGraph_Person (UserId, FullName, TenantId)
            VALUES (?, ?, ?)
            """,
            user["UserId"],
            user["FullName"],
            tenant_id,
        )
    for event in nominations:
        cursor.execute(
            """
            INSERT INTO integrity.NomGraph_Nominated
                ($from_id, $to_id, NominationId, Amount, Status, NomDate)
            SELECT source.$node_id, target.$node_id, ?, ?, ?, CAST(? AS DATE)
            FROM integrity.NomGraph_Person AS source
            JOIN integrity.NomGraph_Person AS target
              ON target.UserId = ? AND target.TenantId = ?
            WHERE source.UserId = ? AND source.TenantId = ?
            """,
            event["NominationId"],
            event["Amount"],
            event["Status"],
            event["CreatedAt"],
            event["BeneficiaryId"],
            tenant_id,
            event["NominatorId"],
            tenant_id,
        )
