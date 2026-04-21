"""
skills/schema/tools.py
───────────────────────
Tools owned by the schema skill:
  • query_database         — execute a T-SQL SELECT written by the LLM
  • get_analytics_overview — broad pre-computed analytics summary

Both enforce tenant isolation.  query_database stores its last result so
the export skill's tools can inject rows when the LLM forgets to pass them.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import sqlhelper2 as sqlhelper

logger = logging.getLogger(__name__)

# Shared mutable state: last rows fetched, available to exports skill
_last_query_rows: list[dict] = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_rows(rows: list, columns: list[str] | None = None) -> list[dict]:
    """Convert list[tuple] → list[dict]. Pass-through if already dicts."""
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return rows
    if columns and len(columns) == len(rows[0]):
        return [dict(zip(columns, row)) for row in rows]
    return [{f"col_{i}": v for i, v in enumerate(row)} for row in rows]


# ── Tool implementations ──────────────────────────────────────────────────────

async def _query_database(sql: str, tenant_id: int = 0) -> dict[str, Any]:
    global _last_query_rows
    if tenant_id and not re.search(r'\bTenantId\b', sql, re.IGNORECASE):
        msg = (
            "Query rejected by tenant isolation guard: the SQL does not contain "
            f"a TenantId filter. Add WHERE <alias>.TenantId = {tenant_id} and retry."
        )
        logger.error("tool:query_database — TENANT GUARD rejected (tenant_id=%d)", tenant_id)
        _last_query_rows = []
        return {"status": "error", "message": msg, "rows": [], "sql": sql}
    try:
        raw_rows, columns = sqlhelper.run_query_with_columns(sql)
        rows = _normalise_rows(raw_rows, columns)
        _last_query_rows = rows
        logger.info("tool:query_database — %d rows (tenant_id=%d)", len(rows), tenant_id)
        return {"status": "success", "sql": sql, "row_count": len(rows), "rows": rows[:200]}
    except Exception as err:
        logger.error("tool:query_database — failed: %s", err)
        _last_query_rows = []
        return {"status": "error", "message": str(err), "rows": [], "sql": sql}


async def _get_analytics_overview(tenant_id: int = 0) -> dict[str, Any]:
    try:
        return {
            "status":             "success",
            "overview":           sqlhelper.get_analytics_overview(tenant_id),
            "approval_metrics":   sqlhelper.get_approval_metrics(tenant_id),
            "diversity_metrics":  sqlhelper.get_diversity_metrics(tenant_id),
            "department_spending": [
                {"department": d[0], "awards": d[1], "total": d[2], "avg": d[3]}
                for d in sqlhelper.get_department_spending(tenant_id)
            ],
            "top_recipients": [
                {"id": r[0], "first": r[1], "last": r[2], "awards": r[3], "total": r[4]}
                for r in sqlhelper.get_top_recipients(tenant_id, limit=5)
            ],
            "top_nominators": [
                {"id": n[0], "first": n[1], "last": n[2], "nominations": n[3], "total": n[4]}
                for n in sqlhelper.get_top_nominators(tenant_id, limit=5)
            ],
        }
    except Exception as err:
        logger.error("tool:get_analytics_overview — failed: %s", err)
        return {"status": "error", "message": str(err)}


# ── OpenAI tool schemas ───────────────────────────────────────────────────────

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": (
                "Execute a T-SQL SELECT query against the award nomination database. "
                "Write the T-SQL yourself based on the schema in your instructions. "
                "Only SELECT is permitted — never INSERT, UPDATE, DELETE, DROP, ALTER, "
                "EXEC, TRUNCATE, or MERGE."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A valid T-SQL SELECT. No semicolons. No markdown."
                    }
                },
                "required": ["sql"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_analytics_overview",
            "description": (
                "Retrieve a broad analytics summary: totals, approval rates, diversity "
                "metrics, top recipients/nominators, and department breakdown. "
                "Use for open-ended or high-level questions that don't map to a single "
                "SQL query, or as supplementary context."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
]

IMPLEMENTATIONS = {
    "query_database":         _query_database,
    "get_analytics_overview": _get_analytics_overview,
}
