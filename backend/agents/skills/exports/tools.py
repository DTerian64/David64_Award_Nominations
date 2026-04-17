"""
skills/exports/tools.py
────────────────────────
Tools owned by the exports skill:
  • export_to_excel
  • export_to_pdf
  • export_to_csv

Each tool falls back to the last rows fetched by query_database when the
LLM forgets to pass rows explicitly.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.exporters import export_excel, export_pdf, export_csv
from agents.skills.schema.tools import _last_query_rows  # shared state

logger = logging.getLogger(__name__)


# ── Tool implementations ──────────────────────────────────────────────────────

async def _export_to_excel(
    question: str,
    answer: str,
    rows: list[dict] | None = None,
    filename: str | None = None,
    tenant_id: int = 0,
) -> dict[str, Any]:
    if not rows:
        logger.warning(
            "tool:export_to_excel — LLM omitted rows, injecting last query (%d rows)",
            len(_last_query_rows),
        )
        rows = _last_query_rows
    logger.info("tool:export_to_excel — %d rows", len(rows))
    return await export_excel(question=question, answer=answer, rows=rows, filename=filename)


async def _export_to_pdf(
    question: str,
    answer: str,
    rows: list[dict] | None = None,
    filename: str | None = None,
    tenant_id: int = 0,
) -> dict[str, Any]:
    if not rows:
        logger.warning(
            "tool:export_to_pdf — LLM omitted rows, injecting last query (%d rows)",
            len(_last_query_rows),
        )
        rows = _last_query_rows
    logger.info("tool:export_to_pdf — %d rows", len(rows) if rows else 0)
    return await export_pdf(question=question, answer=answer, rows=rows, filename=filename)


async def _export_to_csv(
    rows: list[dict] | None = None,
    filename: str | None = None,
    tenant_id: int = 0,
) -> dict[str, Any]:
    if not rows:
        logger.warning(
            "tool:export_to_csv — LLM omitted rows, injecting last query (%d rows)",
            len(_last_query_rows),
        )
        rows = _last_query_rows
    logger.info("tool:export_to_csv — %d rows", len(rows))
    return await export_csv(rows=rows, filename=filename)


# ── OpenAI tool schemas ───────────────────────────────────────────────────────

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "export_to_excel",
            "description": (
                "Generate an Excel (.xlsx) file from query results, upload to blob storage, "
                "and return a download URL. Use when the user asks for an Excel file, "
                "spreadsheet, or workbook. Always call query_database first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The original user question."},
                    "answer":   {"type": "string", "description": "The analysis text to include."},
                    "rows":     {"type": "array", "items": {"type": "object"},
                                 "description": "Exact rows from query_database."},
                    "filename": {"type": "string", "description": "Optional filename without extension."},
                },
                "required": ["question", "answer", "rows"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "export_to_pdf",
            "description": (
                "Generate a PDF report and upload to blob storage. "
                "Always call query_database first and pass its rows here. "
                "Never call with an empty rows array."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The original user question."},
                    "answer":   {"type": "string", "description": "The analysis text to include."},
                    "rows":     {"type": "array", "items": {"type": "object"},
                                 "description": "REQUIRED — exact rows from query_database."},
                    "filename": {"type": "string", "description": "Optional filename without extension."},
                },
                "required": ["question", "answer", "rows"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "export_to_csv",
            "description": (
                "Generate a CSV file from query results and upload to blob storage. "
                "Use when the user asks for a CSV or raw data download."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "rows":     {"type": "array", "items": {"type": "object"},
                                 "description": "Rows from query_database."},
                    "filename": {"type": "string", "description": "Optional filename without extension."},
                },
                "required": ["rows"]
            }
        }
    },
]

IMPLEMENTATIONS = {
    "export_to_excel": _export_to_excel,
    "export_to_pdf":   _export_to_pdf,
    "export_to_csv":   _export_to_csv,
}
