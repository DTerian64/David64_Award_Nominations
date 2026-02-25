"""
agents/exporters/pdf.py
────────────────────────
Builds an in-memory PDF report from question, answer, and optional data rows.
Returns (bytes, filename) — no I/O, no blob, purely functional.

Uses reportlab (lightweight, no system dependencies).
Add to requirements.txt: reportlab>=4.0.0,<5.0.0
"""

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)


def build_pdf(
    question: str,
    answer: str,
    rows: list[dict] | None = None,
    filename: str | None = None,
) -> tuple[bytes, str]:
    """
    Build a PDF report with:
      - Header (title + timestamp)
      - Question section
      - Analysis/answer section
      - Optional data table

    Returns (pdf_bytes, filename).
    """
    fname = (filename or f"analytics_report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}") + ".pdf"

    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=letter,
        leftMargin=inch,
        rightMargin=inch,
        topMargin=inch,
        bottomMargin=inch,
    )

    styles = getSampleStyleSheet()
    title_style  = ParagraphStyle("Title",  parent=styles["Heading1"], fontSize=16, spaceAfter=6)
    label_style  = ParagraphStyle("Label",  parent=styles["Heading2"], fontSize=11, spaceAfter=4, textColor=colors.HexColor("#2E75B6"))
    body_style   = ParagraphStyle("Body",   parent=styles["Normal"],   fontSize=10, spaceAfter=6, leading=14)
    footer_style = ParagraphStyle("Footer", parent=styles["Normal"],   fontSize=8,  textColor=colors.grey)

    story = []

    # ── Title ─────────────────────────────────────────────────────────────────
    story.append(Paragraph("Award Nomination Analytics Report", title_style))
    story.append(Paragraph(
        f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        footer_style
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#2E75B6"), spaceAfter=12))

    # ── Question ──────────────────────────────────────────────────────────────
    story.append(Paragraph("Question", label_style))
    story.append(Paragraph(question, body_style))
    story.append(Spacer(1, 8))

    # ── Answer ────────────────────────────────────────────────────────────────
    story.append(Paragraph("Analysis", label_style))
    # Split on newlines so paragraphs render correctly
    for para in answer.split("\n"):
        para = para.strip()
        if para:
            story.append(Paragraph(para, body_style))
    story.append(Spacer(1, 12))

    # ── Data table ────────────────────────────────────────────────────────────
    if rows:
        story.append(Paragraph("Data", label_style))
        story.append(Spacer(1, 4))

        headers = list(rows[0].keys())
        table_data = [headers] + [[str(row.get(h, "")) for h in headers] for row in rows[:500]]

        col_width = (6.5 * inch) / max(len(headers), 1)
        tbl = Table(table_data, colWidths=[col_width] * len(headers), repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0),  colors.HexColor("#2E75B6")),
            ("TEXTCOLOR",   (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",    (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",    (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
            ("GRID",        (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
            ("VALIGN",      (0, 0), (-1, -1), "TOP"),
            ("PADDING",     (0, 0), (-1, -1), 4),
        ]))
        story.append(tbl)

    doc.build(story)
    return output.getvalue(), fname