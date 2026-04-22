"""
skills/integrity/tools.py
──────────────────────────
Tools owned by the integrity skill:
  • export_finding_to_excel — fetch a GraphPatternFinding by ID,
                               build a formatted .xlsx workbook, upload to
                               blob storage, return a SAS download URL.

Reuses the existing helpers that already power the Integrity tab's
download button (/api/admin/analytics/integrity/findings/{id}/export):
  • sqlhelper2.get_finding_with_nominations()  — DB query
  • export_utils.build_finding_workbook()       — workbook builder
  • agents.skills.exports.blob_storage.upload_to_blob() — storage + SAS
"""

from __future__ import annotations

import logging
from typing import Any

import sqlhelper2
from export_utils import build_finding_workbook
from agents.skills.exports.blob_storage import upload_to_blob

logger = logging.getLogger(__name__)


# ── Tool implementation ────────────────────────────────────────────────────────

async def _export_finding_to_excel(
    finding_id: int,
    tenant_id:  int = 0,
) -> dict[str, Any]:
    """
    Export a single integrity finding to Excel.

    1. Fetch the finding + its associated nominations from the DB.
    2. Build the formatted workbook (same logic as the Integrity tab button).
    3. Upload to blob storage and return a SAS download URL.
    """
    logger.info("tool:export_finding_to_excel — finding_id=%d tenant_id=%d", finding_id, tenant_id)

    data = sqlhelper2.get_finding_with_nominations(finding_id, tenant_id)
    if data is None:
        return {
            "status":  "error",
            "message": f"Finding {finding_id} not found or does not belong to this tenant.",
        }

    try:
        buf = build_finding_workbook(data)
        buf.seek(0)   # ensure position is at start after openpyxl write
        filename = f"finding_{finding_id}_export.xlsx"
        result = await upload_to_blob(
            buf.read(),
            filename,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        logger.info(
            "tool:export_finding_to_excel — uploaded %s (%d bytes)",
            filename, result.get("file_size_bytes", 0),
        )
        return result
    except Exception as exc:
        logger.error("export_finding_to_excel failed: %s", exc, exc_info=True)
        return {"status": "error", "message": str(exc)}


# ── OpenAI tool schema ────────────────────────────────────────────────────────

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "export_finding_to_excel",
            "description": (
                "Export a specific integrity finding (fraud pattern detection) to an Excel file. "
                "Fetches the finding and all its associated nominations, builds a formatted "
                "workbook, and returns a download URL. "
                "Use when the user asks to export, download, or save a finding by its ID. "
                "Do NOT call query_database first — this tool handles its own data retrieval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "finding_id": {
                        "type": "integer",
                        "description": "The numeric FindingId of the integrity finding to export.",
                    },
                },
                "required": ["finding_id"],
            },
        },
    },
]

IMPLEMENTATIONS = {
    "export_finding_to_excel": _export_finding_to_excel,
}
