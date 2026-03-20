"""
agents/tools/registry.py
─────────────────────────
Maps tool names (from definitions.py) to their Python implementations.

Each function here is called by the agent loop when the LLM requests a tool.
Functions are plain async callables — no protocol coupling, no MCP, no HTTP.

The registry is the only place that knows about sqlhelper and the exporters.
ask_agent.py just calls dispatch() and gets back a result dict.
"""

import json
import logging
import re
from typing import Any

import sqlhelper2 as sqlhelper  # our custom helper for running SQL queries and fetching analytics data
from ..exporters import export_excel, export_pdf, export_csv   # see exporters/__init__.py

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Individual tool implementations
# ─────────────────────────────────────────────────────────────────────────────
_last_query_rows: list[dict] = []  # module-level var to store last query results for potential export by other tools

async def _query_database(sql: str, tenant_id: int = 0) -> dict[str, Any]:
    global _last_query_rows

    # ── Tenant isolation safety guard ────────────────────────────────────────
    # The LLM is instructed to always include a TenantId filter (Rule 9 in the
    # system prompt).  This server-side check is a hard backstop: if the
    # generated SQL is missing it, we reject rather than leak cross-tenant data.
    if tenant_id and not re.search(r'\bTenantId\b', sql, re.IGNORECASE):
        msg = (
            "Query rejected by tenant isolation guard: the SQL does not contain "
            "a TenantId filter. Add 'WHERE <alias>.TenantId = "
            f"{tenant_id}' and retry."
        )
        logger.error("tool:query_database — TENANT GUARD rejected query (tenant_id=%d): %s", tenant_id, sql)
        _last_query_rows = []
        return {"status": "error", "message": msg, "rows": [], "sql": sql}

    try:
        raw_rows, columns = sqlhelper.run_query_with_columns(sql)
        rows = _normalise_rows(raw_rows, columns)
        _last_query_rows = rows
        logger.info("tool:query_database — %d rows returned (tenant_id=%d)", len(rows), tenant_id)
        return {
            "status": "success",
            "sql": sql,
            "row_count": len(rows),
            "rows": rows[:200],
        }
    except Exception as err:
        logger.error("tool:query_database — query failed: %s", err)
        _last_query_rows = []
        return {"status": "error", "message": str(err), "rows": [], "sql": sql}

async def _get_analytics_overview(tenant_id: int = 0) -> dict[str, Any]:
    """Fetch all broad analytics context in one call, scoped to tenant_id."""
    try:
        overview            = sqlhelper.get_analytics_overview(tenant_id)
        approval_metrics    = sqlhelper.get_approval_metrics(tenant_id)
        diversity_metrics   = sqlhelper.get_diversity_metrics(tenant_id)
        department_spending = sqlhelper.get_department_spending(tenant_id)
        top_recipients      = sqlhelper.get_top_recipients(tenant_id, limit=5)
        top_nominators      = sqlhelper.get_top_nominators(tenant_id, limit=5)

        return {
            "status": "success",
            "overview": overview,
            "approval_metrics": approval_metrics,
            "diversity_metrics": diversity_metrics,
            "department_spending": [
                {"department": d[0], "awards": d[1], "total": d[2], "avg": d[3]}
                for d in department_spending
            ],
            "top_recipients": [
                {"id": r[0], "first": r[1], "last": r[2], "awards": r[3], "total": r[4]}
                for r in top_recipients
            ],
            "top_nominators": [
                {"id": n[0], "first": n[1], "last": n[2], "nominations": n[3], "total": n[4]}
                for n in top_nominators
            ],
        }
    except Exception as err:
        logger.error("tool:get_analytics_overview — failed: %s", err)
        return {"status": "error", "message": str(err)}


async def _export_to_excel(
    question: str,
    answer: str,
    rows: list[dict],
    filename: str | None = None,
    tenant_id: int = 0,   # passed through from dispatch; not used by exporter
) -> dict[str, Any]:
    global _last_query_rows
    if not rows:
        logger.warning("tool:export_to_excel — LLM omitted rows, injecting from last query (%d rows)", len(_last_query_rows))
        rows = _last_query_rows
    logger.info("tool:export_to_excel — %d rows", len(rows))
    return await export_excel(question=question, answer=answer, rows=rows, filename=filename)


async def _export_to_pdf(
    question: str,
    answer: str,
    rows: list[dict] | None = None,
    filename: str | None = None,
    tenant_id: int = 0,   # passed through from dispatch; not used by exporter
) -> dict[str, Any]:
    global _last_query_rows
    if not rows:
        logger.warning("tool:export_to_pdf — LLM omitted rows, injecting from last query (%d rows)", len(_last_query_rows))
        rows = _last_query_rows
    logger.info("tool:export_to_pdf — rows=%s", len(rows) if rows else 0)
    if not rows:
        logger.warning("tool:export_to_pdf — called with empty rows, check LLM tool call arguments")
    return await export_pdf(question=question, answer=answer, rows=rows, filename=filename)


async def _export_to_csv(
    rows: list[dict],
    filename: str | None = None,
    tenant_id: int = 0,   # passed through from dispatch; not used by exporter
) -> dict[str, Any]:
    global _last_query_rows
    if not rows:
        logger.warning("tool:export_to_csv — LLM omitted rows, injecting from last query (%d rows)", len(_last_query_rows))
        rows = _last_query_rows
    logger.info("tool:export_to_csv — %d rows", len(rows))
    return await export_csv(rows=rows, filename=filename)


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch table  —  tool name → callable
# ─────────────────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, Any] = {
    "query_database":       _query_database,
    "get_analytics_overview": _get_analytics_overview,
    "export_to_excel":      _export_to_excel,
    "export_to_pdf":        _export_to_pdf,
    "export_to_csv":        _export_to_csv,
}


async def dispatch(tool_name: str, tool_args: dict, tenant_id: int = 0) -> str:
    """
    Called by the agent loop for every tool_call the LLM requests.
    tenant_id is injected into every tool call so implementations can enforce
    row-level tenant isolation without the LLM needing to pass it explicitly.

    Returns a JSON string — this is what gets appended to the message history
    as a 'tool' role message so the LLM can see the result.
    """
    logger.info("tool:dispatch — %s called with args: %s (tenant_id=%d)", tool_name, json.dumps(tool_args, default=str), tenant_id)

    fn = _REGISTRY.get(tool_name)
    if fn is None:
        logger.warning("tool:dispatch — unknown tool requested: %s", tool_name)
        result = {"status": "error", "message": f"Unknown tool: {tool_name}"}
    else:
        try:
            result = await fn(**tool_args, tenant_id=tenant_id)
        except TypeError as te:
            # Argument mismatch — LLM sent wrong params
            logger.error("tool:dispatch — bad args for %s: %s | args=%s", tool_name, te, tool_args)
            result = {"status": "error", "message": f"Invalid arguments for {tool_name}: {te}"}
        except Exception as err:
            logger.error("tool:dispatch — %s raised: %s", tool_name, err, exc_info=True)
            result = {"status": "error", "message": str(err)}

    return json.dumps(result, default=str)   # default=str handles dates, Decimals etc.


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_rows(rows: list, columns: list[str] | None = None) -> list[dict]:
    """Convert list[tuple] → list[dict]. Pass-through if already dicts."""
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return rows
    if columns and len(columns) == len(rows[0]):
        return [dict(zip(columns, row)) for row in rows]
    return [{f"col_{i}": v for i, v in enumerate(row)} for row in rows]