"""
agents/exporters/__init__.py
─────────────────────────────
Plain async functions that generate export files and upload them to blob storage.

No MCP, no subprocess, no protocol — just Python.
Called by tools/registry.py when the LLM requests an export tool.

Each function returns a dict:
    { "status": "success", "download_url": "...", "file_size_bytes": int }
    { "status": "error",   "message": "..." }

Actual file-building logic lives in excel.py / pdf.py / csv_writer.py.
Blob upload logic lives in blob_storage.py.
"""

from .excel       import build_excel
from .pdf         import build_pdf
from .csv_writer  import build_csv
from .blob_storage import upload_to_blob

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def export_excel(
    question: str,
    answer: str,
    rows: list[dict],
    filename: str | None = None,
) -> dict[str, Any]:
    try:
        data, fname = build_excel(question=question, answer=answer, rows=rows, filename=filename)
        return await upload_to_blob(data, fname, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        logger.error("export_excel failed: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}


async def export_pdf(
    question: str,
    answer: str,
    rows: list[dict] | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    try:
        data, fname = build_pdf(question=question, answer=answer, rows=rows, filename=filename)
        return await upload_to_blob(data, fname, content_type="application/pdf")
    except Exception as e:
        logger.error("export_pdf failed: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}


async def export_csv(
    rows: list[dict],
    filename: str | None = None,
) -> dict[str, Any]:
    try:
        data, fname = build_csv(rows=rows, filename=filename)
        return await upload_to_blob(data, fname, content_type="text/csv")
    except Exception as e:
        logger.error("export_csv failed: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}