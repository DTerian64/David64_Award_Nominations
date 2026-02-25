"""
agents/exporters/excel.py
──────────────────────────
Builds an in-memory Excel workbook from question, answer, and data rows.
Returns (bytes, filename) — no I/O, no blob, purely functional.
"""

import io
from datetime import datetime

import pandas as pd


def build_excel(
    question: str,
    answer: str,
    rows: list[dict],
    filename: str | None = None,
) -> tuple[bytes, str]:
    """
    Build an Excel workbook with two sheets:
      - Summary: question + LLM answer
      - Data:    the rows as a formatted table

    Returns (xlsx_bytes, filename).
    """
    fname = (filename or f"analytics_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}") + ".xlsx"

    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        # ── Sheet 1: Summary ──────────────────────────────────────────────────
        summary_df = pd.DataFrame({
            "": ["Question", "Answer", "Generated"],
            " ": [
                question,
                answer,
                datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            ]
        })
        summary_df.to_excel(writer, sheet_name="Summary", index=False)

        ws_summary = writer.sheets["Summary"]
        ws_summary.column_dimensions["A"].width = 15
        ws_summary.column_dimensions["B"].width = 80

        # ── Sheet 2: Data ─────────────────────────────────────────────────────
        if rows:
            data_df = pd.DataFrame(rows)
            data_df.to_excel(writer, sheet_name="Data", index=False)

            ws_data = writer.sheets["Data"]
            for col in ws_data.columns:
                max_len = max(len(str(col[0].value or "")),
                              *(len(str(cell.value or "")) for cell in col[1:]))
                ws_data.column_dimensions[col[0].column_letter].width = min(max_len + 4, 50)

    return output.getvalue(), fname