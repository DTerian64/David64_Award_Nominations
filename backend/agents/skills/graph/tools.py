"""
skills/graph/tools.py
──────────────────────
Tools owned by the graph skill — SQL Server MATCH queries against the
nomination graph and GraphPatternFindings integrity results.

  • graph_search_user               resolve a name → UserId
  • graph_get_nominations_sent      out-edges for a user
  • graph_get_nominations_received  in-edges for a user
  • graph_get_network               neighbourhood traversal (SHORTEST_PATH)
  • graph_find_path                 shortest directed path between two users
  • graph_get_degree_leaders        top nominators or beneficiaries by count
  • graph_get_integrity_findings    query GraphPatternFindings with filters

All queries are parameterised — the LLM passes typed arguments only,
never raw SQL, making these tools immune to prompt-injection SQL attacks.

Tenant isolation: NomGraph_Person.TenantId is the isolation boundary;
filtering the source node's TenantId is sufficient because edges only
connect nodes within the same tenant.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

import sqlhelper2 as sqlhelper

logger = logging.getLogger(__name__)


# ── Tool implementations ──────────────────────────────────────────────────────

async def _graph_search_user(name_fragment: str, tenant_id: int = 0) -> dict[str, Any]:
    try:
        with sqlhelper.get_db_context() as session:
            rows = session.execute(text("""
                SELECT TOP 20 p.UserId, p.FullName
                FROM   dbo.NomGraph_Person p
                WHERE  p.TenantId = :tid
                  AND  LOWER(p.FullName) LIKE LOWER(:pat)
                ORDER BY p.FullName
            """), {"tid": tenant_id, "pat": f"%{name_fragment}%"}).fetchall()
        results = [{"UserId": r[0], "FullName": r[1]} for r in rows]
        logger.info("tool:graph_search_user — '%s' → %d match(es)", name_fragment, len(results))
        return {"status": "success", "matches": results, "count": len(results)}
    except Exception as e:
        logger.error("tool:graph_search_user — %s", e)
        return {"status": "error", "message": str(e)}


async def _graph_get_nominations_sent(
    user_id: int, tenant_id: int = 0, limit: int = 50,
) -> dict[str, Any]:
    try:
        with sqlhelper.get_db_context() as session:
            rows = session.execute(text(f"""
                SELECT TOP {min(limit, 200)}
                       p2.UserId, p2.FullName,
                       e.NominationId, e.Amount, e.Status, e.NomDate
                FROM   dbo.NomGraph_Person p1, dbo.NomGraph_Nominated e, dbo.NomGraph_Person p2
                WHERE  MATCH(p1-(e)->p2)
                  AND  p1.TenantId = :tid AND p1.UserId = :uid
                ORDER BY e.NomDate DESC
            """), {"tid": tenant_id, "uid": user_id}).fetchall()
        results = [
            {"ToUserId": r[0], "ToFullName": r[1], "NominationId": r[2],
             "Amount": r[3], "Status": r[4], "NomDate": str(r[5]) if r[5] else None}
            for r in rows
        ]
        logger.info("tool:graph_get_nominations_sent — user=%d → %d edge(s)", user_id, len(results))
        return {"status": "success", "user_id": user_id, "nominations": results, "count": len(results)}
    except Exception as e:
        logger.error("tool:graph_get_nominations_sent — %s", e)
        return {"status": "error", "message": str(e)}


async def _graph_get_nominations_received(
    user_id: int, tenant_id: int = 0, limit: int = 50,
) -> dict[str, Any]:
    try:
        with sqlhelper.get_db_context() as session:
            rows = session.execute(text(f"""
                SELECT TOP {min(limit, 200)}
                       p1.UserId, p1.FullName,
                       e.NominationId, e.Amount, e.Status, e.NomDate
                FROM   dbo.NomGraph_Person p1, dbo.NomGraph_Nominated e, dbo.NomGraph_Person p2
                WHERE  MATCH(p1-(e)->p2)
                  AND  p2.TenantId = :tid AND p2.UserId = :uid
                ORDER BY e.NomDate DESC
            """), {"tid": tenant_id, "uid": user_id}).fetchall()
        results = [
            {"FromUserId": r[0], "FromFullName": r[1], "NominationId": r[2],
             "Amount": r[3], "Status": r[4], "NomDate": str(r[5]) if r[5] else None}
            for r in rows
        ]
        logger.info("tool:graph_get_nominations_received — user=%d → %d edge(s)", user_id, len(results))
        return {"status": "success", "user_id": user_id, "nominations": results, "count": len(results)}
    except Exception as e:
        logger.error("tool:graph_get_nominations_received — %s", e)
        return {"status": "error", "message": str(e)}


async def _graph_get_network(
    user_id: int, depth: int = 1, tenant_id: int = 0,
) -> dict[str, Any]:
    depth = max(1, min(depth, 3))
    try:
        with sqlhelper.get_db_context() as session:
            rows = session.execute(text("""
                SELECT DestUserId, DestFullName, Hops FROM (
                    SELECT
                        LAST_VALUE(dest.UserId)   WITHIN GROUP (GRAPH PATH) AS DestUserId,
                        LAST_VALUE(dest.FullName) WITHIN GROUP (GRAPH PATH) AS DestFullName,
                        COUNT(e.NominationId)     WITHIN GROUP (GRAPH PATH) AS Hops
                    FROM   dbo.NomGraph_Person src,
                           dbo.NomGraph_Nominated FOR PATH e,
                           dbo.NomGraph_Person    FOR PATH dest
                    WHERE  MATCH(SHORTEST_PATH(src(-(e)->dest)+))
                      AND  src.UserId = :uid AND src.TenantId = :tid
                ) sub WHERE Hops <= :depth ORDER BY Hops, DestUserId
            """), {"uid": user_id, "tid": tenant_id, "depth": depth}).fetchall()
        nodes = [{"UserId": r[0], "FullName": r[1], "Hops": r[2]} for r in rows]
        logger.info("tool:graph_get_network — user=%d depth=%d → %d node(s)", user_id, depth, len(nodes))
        return {"status": "success", "source_user": user_id, "depth": depth,
                "nodes": nodes, "count": len(nodes)}
    except Exception as e:
        logger.error("tool:graph_get_network — %s", e)
        return {"status": "error", "message": str(e)}


async def _graph_find_path(
    user_id_a: int, user_id_b: int, tenant_id: int = 0,
) -> dict[str, Any]:
    try:
        with sqlhelper.get_db_context() as session:
            row = session.execute(text("""
                SELECT Path, Hops FROM (
                    SELECT
                        CAST(:uid_a AS NVARCHAR(MAX)) + '->' +
                        STRING_AGG(CAST(dest.UserId AS NVARCHAR(MAX)), '->')
                            WITHIN GROUP (GRAPH PATH) AS Path,
                        COUNT(e.NominationId) WITHIN GROUP (GRAPH PATH) AS Hops,
                        LAST_VALUE(dest.UserId) WITHIN GROUP (GRAPH PATH) AS DestUserId
                    FROM   dbo.NomGraph_Person src,
                           dbo.NomGraph_Nominated FOR PATH e,
                           dbo.NomGraph_Person    FOR PATH dest
                    WHERE  MATCH(SHORTEST_PATH(src(-(e)->dest)+))
                      AND  src.UserId = :uid_a AND src.TenantId = :tid
                ) sub WHERE DestUserId = :uid_b ORDER BY Hops
            """), {"uid_a": user_id_a, "uid_b": user_id_b, "tid": tenant_id}).fetchone()

        if row:
            return {"status": "success", "connected": True,
                    "from_user": user_id_a, "to_user": user_id_b,
                    "hops": row[1], "path": row[0]}
        return {"status": "success", "connected": False,
                "from_user": user_id_a, "to_user": user_id_b,
                "hops": None, "path": None}
    except Exception as e:
        logger.error("tool:graph_find_path — %s", e)
        return {"status": "error", "message": str(e)}


async def _graph_get_degree_leaders(
    direction: str = "out", limit: int = 10, tenant_id: int = 0,
) -> dict[str, Any]:
    direction = direction if direction in ("out", "in") else "out"
    limit = max(1, min(limit, 50))
    try:
        with sqlhelper.get_db_context() as session:
            pivot = "p1" if direction == "out" else "p2"
            rows = session.execute(text(f"""
                SELECT TOP {limit}
                       {pivot}.UserId, {pivot}.FullName,
                       COUNT(*)      AS NominationCount,
                       SUM(e.Amount) AS TotalAmount
                FROM   dbo.NomGraph_Person p1, dbo.NomGraph_Nominated e, dbo.NomGraph_Person p2
                WHERE  MATCH(p1-(e)->p2) AND p1.TenantId = :tid
                GROUP BY {pivot}.UserId, {pivot}.FullName
                ORDER BY NominationCount DESC, TotalAmount DESC
            """), {"tid": tenant_id}).fetchall()
        results = [{"UserId": r[0], "FullName": r[1],
                    "NominationCount": r[2], "TotalAmount": r[3]} for r in rows]
        logger.info("tool:graph_get_degree_leaders — direction=%s → %d result(s)", direction, len(results))
        return {"status": "success", "direction": direction, "leaders": results, "count": len(results)}
    except Exception as e:
        logger.error("tool:graph_get_degree_leaders — %s", e)
        return {"status": "error", "message": str(e)}


async def _graph_get_integrity_findings(
    pattern_type: str | None = None,
    severity:     str | None = None,
    user_id:      int | None = None,
    finding_id:   int | None = None,
    limit:        int = 20,
    tenant_id:    int = 0,
) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    try:
        conditions = ["TenantId = :tid"]
        params: dict[str, Any] = {"tid": tenant_id, "limit": limit}
        if pattern_type:
            conditions.append("PatternType = :pt");  params["pt"]      = pattern_type
        if severity:
            conditions.append("Severity = :sev");    params["sev"]     = severity
        if finding_id is not None:
            conditions.append("FindingId = :fid");   params["fid"]     = finding_id
        if user_id is not None:
            conditions.append("AffectedUsers LIKE :uid_pat")
            params["uid_pat"] = f"%{user_id}%"

        where = " AND ".join(conditions)
        with sqlhelper.get_db_context() as session:
            rows = session.execute(text(f"""
                SELECT TOP :limit
                       FindingId, PatternType, Severity,
                       AffectedUsers, NominationIds, TotalAmount,
                       Detail, DetectedAt, RunId
                FROM   dbo.GraphPatternFindings
                WHERE  {where}
                ORDER BY
                    CASE Severity
                        WHEN 'Critical' THEN 1 WHEN 'High' THEN 2
                        WHEN 'Medium'   THEN 3 ELSE 4
                    END, DetectedAt DESC
            """), params).fetchall()

        results = [
            {"FindingId": r[0], "PatternType": r[1], "Severity": r[2],
             "AffectedUsers": r[3], "NominationIds": r[4], "TotalAmount": r[5],
             "Detail": r[6], "DetectedAt": str(r[7]) if r[7] else None, "RunId": r[8]}
            for r in rows
        ]
        logger.info("tool:graph_get_integrity_findings — %d finding(s) returned", len(results))
        return {"status": "success", "findings": results, "count": len(results)}
    except Exception as e:
        logger.error("tool:graph_get_integrity_findings — %s", e)
        return {"status": "error", "message": str(e)}


# ── OpenAI tool schemas ───────────────────────────────────────────────────────

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "graph_search_user",
            "description": (
                "Find users in the nomination graph by partial name (case-insensitive). "
                "Always call this first when a question mentions a person by name — "
                "resolve to UserId before calling any other graph tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name_fragment": {"type": "string",
                                     "description": "Part of the person's name (e.g. 'Alice', 'Smith')."}
                },
                "required": ["name_fragment"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "graph_get_nominations_sent",
            "description": "Return all nominations sent BY a user (out-edges). Use to answer: 'Who has User X nominated?'",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "description": "UserId of the nominator."},
                    "limit":   {"type": "integer", "description": "Max results (default 50, max 200)."},
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "graph_get_nominations_received",
            "description": "Return all nominations received BY a user (in-edges). Use to answer: 'Who nominated User X?'",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "description": "UserId of the beneficiary."},
                    "limit":   {"type": "integer", "description": "Max results (default 50, max 200)."},
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "graph_get_network",
            "description": (
                "Return all users reachable from a given user within N directed hops. "
                "depth=1 finds direct connections; depth=2 finds friends-of-friends. "
                "Uses SHORTEST_PATH so results show minimum-hop distances."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "description": "Starting UserId."},
                    "depth":   {"type": "integer", "description": "Hops to traverse (1–3, default 1)."},
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "graph_find_path",
            "description": (
                "Find the shortest directed nomination path from user_id_a to user_id_b. "
                "Returns hop count and intermediate user IDs, or 'not connected' if no path exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id_a": {"type": "integer", "description": "Starting user (nominator end)."},
                    "user_id_b": {"type": "integer", "description": "Ending user (beneficiary end)."},
                },
                "required": ["user_id_a", "user_id_b"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "graph_get_degree_leaders",
            "description": (
                "Return the most active nominators (direction='out') or most-nominated "
                "beneficiaries (direction='in') ranked by count and total amount."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["out", "in"],
                                  "description": "'out' for top nominators, 'in' for top beneficiaries."},
                    "limit":     {"type": "integer", "description": "Results to return (default 10, max 50)."},
                },
                "required": ["direction"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "graph_get_integrity_findings",
            "description": (
                "Query GraphPatternFindings — fraud and integrity patterns from the weekly analytics job. "
                "All filters optional. Use for questions about rings, suspicious clusters, copy-paste, "
                "super-nominators, deserts, approver affinity, transactional language, hidden candidates, "
                "or to look up a specific finding by FindingId."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern_type": {
                        "type": "string",
                        "enum": ["Ring", "SuperNominator", "Desert", "ApproverAffinity",
                                 "CopyPaste", "TransactionalLanguage", "HiddenCandidate"],
                        "description": "Filter to a specific pattern type."
                    },
                    "severity": {
                        "type": "string", "enum": ["Critical", "High", "Medium", "Low"],
                        "description": "Filter to a specific severity."
                    },
                    "user_id":    {"type": "integer",
                                   "description": "Return findings where this UserId appears in AffectedUsers."},
                    "finding_id": {"type": "integer",
                                   "description": "Return the single finding with this FindingId."},
                    "limit":      {"type": "integer",
                                   "description": "Max findings to return (default 20, max 200)."},
                },
                "required": []
            }
        }
    },
]

IMPLEMENTATIONS = {
    "graph_search_user":              _graph_search_user,
    "graph_get_nominations_sent":     _graph_get_nominations_sent,
    "graph_get_nominations_received": _graph_get_nominations_received,
    "graph_get_network":              _graph_get_network,
    "graph_find_path":                _graph_find_path,
    "graph_get_degree_leaders":       _graph_get_degree_leaders,
    "graph_get_integrity_findings":   _graph_get_integrity_findings,
}
