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
from typing import Any

import sqlhelper
from ..exporters import export_excel, export_pdf, export_csv   # see exporters/__init__.py

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Individual tool implementations
# ─────────────────────────────────────────────────────────────────────────────

async def _query_database(sql: str) -> dict[str, Any]:
    try:
        raw_rows = sqlhelper.run_query(sql)
        rows = _normalise_rows(raw_rows)
        logger.info("tool:query_database — %d rows returned", len(rows))
        return {
            "status": "success",
            "sql": sql,
            "row_count": len(rows),
            "rows": rows[:200],
        }
    except Exception as err:
        logger.error("tool:query_database — query failed: %s", err)
        return {"status": "error", "message": str(err), "rows": [], "sql": sql}

async def _get_analytics_overview() -> dict[str, Any]:
    """Fetch all broad analytics context in one call."""
    try:
        overview            = sqlhelper.get_analytics_overview()
        approval_metrics    = sqlhelper.get_approval_metrics()
        diversity_metrics   = sqlhelper.get_diversity_metrics()
        department_spending = sqlhelper.get_department_spending()
        top_recipients      = sqlhelper.get_top_recipients(limit=5)
        top_nominators      = sqlhelper.get_top_nominators(limit=5)

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
) -> dict[str, Any]:
    logger.info("tool:export_to_excel — %d rows", len(rows))
    return await export_excel(question=question, answer=answer, rows=rows, filename=filename)


async def _export_to_pdf(
    question: str,
    answer: str,
    rows: list[dict] | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    logger.info("tool:export_to_pdf — rows=%s", len(rows) if rows else 0)
    return await export_pdf(question=question, answer=answer, rows=rows, filename=filename)


async def _export_to_csv(
    rows: list[dict],
    filename: str | None = None,
) -> dict[str, Any]:
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


async def dispatch(tool_name: str, tool_args: dict) -> str:
    """
    Called by the agent loop for every tool_call the LLM requests.

    Returns a JSON string — this is what gets appended to the message history
    as a 'tool' role message so the LLM can see the result.
    """
    fn = _REGISTRY.get(tool_name)
    if fn is None:
        logger.warning("tool:dispatch — unknown tool requested: %s", tool_name)
        result = {"status": "error", "message": f"Unknown tool: {tool_name}"}
    else:
        try:
            result = await fn(**tool_args)
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

def _normalise_rows(rows: list) -> list[dict]:
    """Convert list[tuple] → list[dict]. Pass-through if already dicts."""
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return rows
    return [{f"col_{i}": v for i, v in enumerate(row)} for row in rows]