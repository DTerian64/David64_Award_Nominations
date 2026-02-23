"""
mcp_export_client.py
────────────────────
Thin async client that spawns the Analytics Export MCP server as a subprocess
and calls its tools (export_to_excel, export_to_pdf, export_to_csv).

Used by /api/admin/analytics/ask in main.py when the user requests an export.

Usage:
    from mcp_export_client import export_analytics

    result = await export_analytics(
        format   = "excel",          # "excel" | "pdf" | "csv"
        question = req.question,
        answer   = llm_answer,
        rows     = sql_rows,         # list[dict] or None
        filename = None,             # optional custom filename
    )
    # result = { "status": "success", "file_path": "...", "file_size_bytes": ... }
"""

import os
import json
import logging
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

# Path to the MCP server script
# Structure: Award_Nomination_App/
#   ├── backend/agents/mcp_export_client.py  (this file)
#   └── MCP_Servers/Analytics_Export_Service/server.py
_SERVER_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),   # backend/agents/
        "..", "..",                  # up to Award_Nomination_App/
        "MCP_Servers", "Analytics_Export_Service", "server.py"
    )
)

_SERVER_PARAMS = StdioServerParameters(
    command="python",
    args=[_SERVER_PATH],
    env={**os.environ},   # pass through all env vars (EXPORT_BASE_PATH etc.)
)

# Map friendly format names → MCP tool names
_FORMAT_TO_TOOL = {
    "excel": "export_to_excel",
    "xlsx":  "export_to_excel",
    "pdf":   "export_to_pdf",
    "csv":   "export_to_csv",
}


def _rows_to_dicts(rows: list, columns: list[str] | None = None) -> list[dict]:
    """
    Convert raw SQL rows (list of tuples) to list of dicts for the MCP server.
    If rows are already dicts, pass through unchanged.
    """
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return rows
    # Tuple rows — use column names if provided, else col_0, col_1 ...
    if columns:
        return [dict(zip(columns, row)) for row in rows]
    return [
        {f"col_{i}": v for i, v in enumerate(row)}
        for row in rows
    ]


async def export_analytics(
    format: str,
    question: str,
    answer: str,
    rows: list | None = None,
    columns: list[str] | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """
    Spawn the MCP export server, call the appropriate export tool, return result.

    Args:
        format:    "excel", "xlsx", "pdf", or "csv"
        question:  The user's original question (written into the export)
        answer:    The LLM-generated answer (written into the export)
        rows:      SQL result rows — list of tuples or list of dicts
        columns:   Column names for the rows (used when rows are tuples)
        filename:  Optional custom filename (without extension)

    Returns:
        dict with keys: status, file_path, file_size_bytes, rows_exported
        On error: dict with keys: status="error", message=...
    """
    tool_name = _FORMAT_TO_TOOL.get(format.lower())
    if not tool_name:
        return {
            "status": "error",
            "message": f"Unsupported export format: '{format}'. Use excel, pdf, or csv."
        }

    # CSV requires data_table; warn if missing
    if tool_name == "export_to_csv" and not rows:
        return {
            "status": "error",
            "message": "CSV export requires data rows but none were provided."
        }

    data_table = _rows_to_dicts(rows, columns) if rows else None

    # Build tool arguments
    tool_args: dict[str, Any] = {
        "question": question,
        "answer":   answer,
    }
    if data_table:
        tool_args["data_table"] = data_table
    if filename:
        tool_args["filename"] = filename

    # CSV tool only accepts data_table + filename
    if tool_name == "export_to_csv":
        tool_args = {k: tool_args[k] for k in ("data_table", "filename") if k in tool_args}

    logger.info("mcp_export_client: calling tool '%s' on MCP server at %s", tool_name, _SERVER_PATH)

    try:
        async with stdio_client(_SERVER_PARAMS) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, tool_args)

        # MCP returns TextContent — parse the JSON payload
        raw_text = result.content[0].text
        parsed   = json.loads(raw_text)

        if parsed.get("status") == "success":
            logger.info(
                "mcp_export_client: export succeeded → %s (%d bytes)",
                parsed.get("download_url"), parsed.get("file_size_bytes", 0)
            )
        else:
            logger.warning("mcp_export_client: export returned error: %s", parsed)

        return parsed

    except Exception as e:
        logger.error("mcp_export_client: MCP call failed: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}


def detect_export_format(question: str) -> str | None:
    """
    Detect whether the user is requesting an export and in what format.
    Returns "excel", "pdf", "csv", or None.

    Called by main.py before the LLM so we know early whether to export.
    """
    q = question.lower()

    if any(w in q for w in ("excel", "spreadsheet", "xlsx", "workbook")):
        return "excel"
    if any(w in q for w in ("pdf", "report", "document")):
        return "pdf"
    if any(w in q for w in ("csv", "comma separated", "comma-separated", "download data")):
        return "csv"

    return None