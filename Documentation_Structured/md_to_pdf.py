#!/usr/bin/env python3
r"""
md_to_pdf.py
============
Convert every Markdown file under Documentation_Codex into a matching PDF
(mirroring the folder structure) with linked SVG diagrams embedded inline.

Output:
    Documentation_Codex/PDFs/<same subfolders>/<same name>.pdf

Every page carries a footer:
    left   = Terian Services Inc.
    center = page number
    right  = Award Nomination System

Supported Markdown: ATX headings (#..######), paragraphs, bullet/numbered lists
(with simple nesting), GitHub-style pipe tables, fenced code blocks (```),
horizontal rules, block-quotes, and inline **bold**, *italic*, `code`,
[links](url), plus image links ![alt](path) — SVG images are embedded as scalable
vector graphics (svglib), raster images (PNG/JPG) via reportlab.Image.

Pure-Python — no system libraries required.

    pip install reportlab svglib
    python md_to_pdf.py                 # uses the defaults below
    python md_to_pdf.py --src <dir> --out <dir>

svglib pulls in lxml automatically. Pillow is optional (only for raster images).
"""
from __future__ import annotations

import argparse
import html
import os
import re
import sys
import traceback

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle,
    Preformatted, HRFlowable, KeepTogether, Image as RLImage,
)

try:
    from svglib.svglib import svg2rlg
except Exception:  # pragma: no cover
    svg2rlg = None

# ─────────────────────────────────────────────────────────────────────────────
# Configuration (edit here if paths/branding change)
# ─────────────────────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.join(HERE, "Markdowns")        # Documentation_Codex/Markdowns (source .md tree)
DEFAULT_OUT = os.path.join(HERE, "PDFs")             # Documentation_Codex/PDFs (mirrors Markdowns/)

COMPANY_NAME = "Terian Services Inc."
SYSTEM_NAME = "Award Nomination System"

PAGE_SIZE = A4
MARGIN = 20 * mm
FOOTER_Y = 12 * mm

# Draw a separator rule beneath headings up to this level (1 = H1 only,
# 2 = H1+H2, 3 = H1..H3, 0 = never).
HEADING_RULE_MAX_LEVEL = 2

# ─────────────────────────────────────────────────────────────────────────────
# Styles
# ─────────────────────────────────────────────────────────────────────────────
def build_styles() -> dict:
    ss = getSampleStyleSheet()
    styles = {}
    base_font = "Helvetica"
    styles["body"] = ParagraphStyle(
        "body", parent=ss["BodyText"], fontName=base_font, fontSize=10,
        leading=14, spaceBefore=2, spaceAfter=6, alignment=TA_LEFT,
    )
    for lvl, size, before in [(1, 20, 4), (2, 15, 12), (3, 12.5, 10),
                              (4, 11, 8), (5, 10.5, 6), (6, 10, 6)]:
        styles[f"h{lvl}"] = ParagraphStyle(
            f"h{lvl}", parent=styles["body"], fontName="Helvetica-Bold",
            fontSize=size, leading=size * 1.25, spaceBefore=before,
            spaceAfter=size * 0.35, textColor=colors.HexColor("#1f3b57"),
            keepWithNext=True,
        )
    styles["li"] = ParagraphStyle(
        "li", parent=styles["body"], spaceBefore=1, spaceAfter=2, leading=13,
    )
    styles["code"] = ParagraphStyle(
        "code", parent=styles["body"], fontName="Courier", fontSize=8.5,
        leading=11, textColor=colors.HexColor("#1a1a1a"),
    )
    styles["cell"] = ParagraphStyle(
        "cell", parent=styles["body"], fontSize=8.5, leading=11,
        spaceBefore=0, spaceAfter=0,
    )
    styles["cell_head"] = ParagraphStyle(
        "cell_head", parent=styles["cell"], fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    styles["caption"] = ParagraphStyle(
        "caption", parent=styles["body"], fontSize=8, leading=10,
        alignment=TA_CENTER, textColor=colors.HexColor("#666666"),
        spaceBefore=3, spaceAfter=10, fontName="Helvetica-Oblique",
    )
    styles["quote"] = ParagraphStyle(
        "quote", parent=styles["body"], leftIndent=12, textColor=colors.HexColor("#444444"),
        borderColor=colors.HexColor("#cccccc"), fontName="Helvetica-Oblique",
    )
    styles["missing"] = ParagraphStyle(
        "missing", parent=styles["body"], fontName="Helvetica-Oblique",
        textColor=colors.HexColor("#b00020"), backColor=colors.HexColor("#fdf0f0"),
        borderPadding=4,
    )
    return styles


# ─────────────────────────────────────────────────────────────────────────────
# Inline Markdown → reportlab mini-markup
# ─────────────────────────────────────────────────────────────────────────────
_CODE_SPAN = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])")
_ITALIC_U = re.compile(r"(?<![_\w])_(?!\s)(.+?)(?<!\s)_(?![_\w])")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_IMG_INLINE = re.compile(r"!\[[^\]]*\]\([^)]+\)")


def render_inline(text: str) -> str:
    """Turn inline Markdown into reportlab Paragraph markup (a small HTML subset)."""
    # Drop any inline image markup (block images are handled separately).
    text = _IMG_INLINE.sub("", text)

    # Protect code spans so their contents are not treated as markup.
    code_store: list[str] = []

    def _stash_code(m):
        code_store.append(m.group(1))
        return f"\x00{len(code_store) - 1}\x00"

    text = _CODE_SPAN.sub(_stash_code, text)

    # Escape XML special characters in the remaining prose.
    text = html.escape(text, quote=False)

    # Links → <a>. (URLs rarely contain markup chars in these docs.)
    text = _LINK.sub(lambda m: f'<a href="{html.escape(m.group(2), quote=True)}" '
                               f'color="#1155cc">{m.group(1)}</a>', text)
    # Bold, then italic.
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _ITALIC.sub(r"<i>\1</i>", text)
    text = _ITALIC_U.sub(r"<i>\1</i>", text)

    # Restore code spans as monospace runs (escaped).
    def _unstash(m):
        raw = code_store[int(m.group(1))]
        return (f'<font face="Courier" color="#b0006a">'
                f'{html.escape(raw, quote=False)}</font>')

    text = re.sub("\x00(\\d+)\x00", _unstash, text)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Image embedding
# ─────────────────────────────────────────────────────────────────────────────
STANDALONE_IMG = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$")


def _scaled_svg(path: str, avail_w: float):
    if svg2rlg is None:
        raise RuntimeError("svglib is not installed (pip install svglib)")
    drawing = svg2rlg(path)
    if drawing is None:
        raise RuntimeError("svg2rlg returned None")
    w = float(drawing.width or avail_w) or avail_w
    h = float(drawing.height or avail_w) or avail_w
    if w > avail_w:
        s = avail_w / w
        drawing.width = w * s
        drawing.height = h * s
        drawing.scale(s, s)
    drawing.hAlign = "CENTER"
    return drawing


def image_flowables(alt: str, src: str, md_dir: str, avail_w: float, styles) -> list:
    """Resolve a linked image relative to the .md file and return flowables."""
    path = src if os.path.isabs(src) else os.path.normpath(os.path.join(md_dir, src))
    if not os.path.exists(path):
        return [Paragraph(f"[missing image: {html.escape(src)}]", styles["missing"]),
                Spacer(1, 6)]
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".svg":
            flow = _scaled_svg(path, avail_w)
        else:
            img = RLImage(path)
            if img.drawWidth > avail_w:
                r = avail_w / img.drawWidth
                img.drawWidth *= r
                img.drawHeight *= r
            img.hAlign = "CENTER"
            flow = img
    except Exception as e:  # pragma: no cover
        return [Paragraph(f"[could not render image {html.escape(os.path.basename(src))}: "
                          f"{html.escape(str(e))}]", styles["missing"]), Spacer(1, 6)]
    out = [flow]
    if alt.strip():
        out.append(Paragraph(html.escape(alt.strip()), styles["caption"]))
    else:
        out.append(Spacer(1, 8))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Block-level Markdown parser → flowables
# ─────────────────────────────────────────────────────────────────────────────
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
HR = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
FENCE = re.compile(r"^\s*(```+|~~~+)(.*)$")
BLOCKQUOTE = re.compile(r"^\s*>\s?(.*)$")


def split_table_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    # split on unescaped pipes
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def build_table(header: list[str], rows: list[list[str]], avail_w: float, styles) -> Table:
    ncols = max(len(header), *(len(r) for r in rows)) if rows else len(header)
    header = header + [""] * (ncols - len(header))
    norm_rows = [r + [""] * (ncols - len(r)) for r in rows]

    # Column widths weighted by longest cell text (capped), normalized to width.
    weights = []
    for c in range(ncols):
        longest = len(header[c])
        for r in norm_rows:
            longest = max(longest, len(r[c]))
        weights.append(min(max(longest, 6), 60))
    total = float(sum(weights)) or 1.0
    col_w = [avail_w * w / total for w in weights]

    data = [[Paragraph(render_inline(h), styles["cell_head"]) for h in header]]
    for r in norm_rows:
        data.append([Paragraph(render_inline(c), styles["cell"]) for c in r])

    t = Table(data, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3b57")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#b8c2cc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f6f9")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    t.hAlign = "LEFT"
    return t


def code_block(lines: list[str], avail_w: float, styles) -> Table:
    text = "\n".join(lines) if lines else " "
    pre = Preformatted(text, styles["code"])
    box = Table([[pre]], colWidths=[avail_w])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f4f4")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return box


def parse_markdown(md: str, md_dir: str, avail_w: float, styles) -> list:
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    flow: list = []
    i, n = 0, len(lines)
    para: list[str] = []

    def flush_para():
        nonlocal para
        if para:
            txt = " ".join(s.strip() for s in para).strip()
            if txt:
                flow.append(Paragraph(render_inline(txt), styles["body"]))
            para = []

    while i < n:
        line = lines[i]

        # Fenced code block
        mfence = FENCE.match(line)
        if mfence:
            flush_para()
            fence = mfence.group(1)[0] * 3
            code_lines = []
            i += 1
            while i < n and not lines[i].lstrip().startswith(fence):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            flow.append(code_block(code_lines, avail_w, styles))
            flow.append(Spacer(1, 6))
            continue

        # Blank line
        if not line.strip():
            flush_para()
            i += 1
            continue

        # Standalone image
        mimg = STANDALONE_IMG.match(line)
        if mimg:
            flush_para()
            flow.extend(image_flowables(mimg.group(1), mimg.group(2), md_dir, avail_w, styles))
            i += 1
            continue

        # Heading (with an optional separator rule underneath)
        mh = HEADING.match(line)
        if mh:
            flush_para()
            lvl = len(mh.group(1))
            flow.append(Paragraph(render_inline(mh.group(2).strip()), styles[f"h{lvl}"]))
            if 1 <= lvl <= HEADING_RULE_MAX_LEVEL:
                flow.append(HRFlowable(
                    width="100%",
                    thickness=(1.1 if lvl == 1 else 0.6),
                    color=colors.HexColor("#1f3b57" if lvl == 1 else "#c9d2db"),
                    spaceBefore=1, spaceAfter=7,
                ))
            i += 1
            continue

        # Horizontal rule
        if HR.match(line):
            flush_para()
            flow.append(Spacer(1, 4))
            flow.append(HRFlowable(width="100%", thickness=0.6,
                                   color=colors.HexColor("#cccccc")))
            flow.append(Spacer(1, 6))
            i += 1
            continue

        # Table: a header row followed by a separator row
        if "|" in line and i + 1 < n and TABLE_SEP.match(lines[i + 1]):
            flush_para()
            header = split_table_row(line)
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(split_table_row(lines[i]))
                i += 1
            flow.append(build_table(header, rows, avail_w, styles))
            flow.append(Spacer(1, 8))
            continue

        # Lists
        if LIST_ITEM.match(line):
            flush_para()
            while i < n and LIST_ITEM.match(lines[i]):
                m = LIST_ITEM.match(lines[i])
                indent = len(m.group(1).replace("\t", "    "))
                marker = m.group(2)
                content = m.group(3).strip()
                # continuation lines (wrapped list text)
                i += 1
                while (i < n and lines[i].strip() and not LIST_ITEM.match(lines[i])
                       and not HEADING.match(lines[i]) and "|" not in lines[i]
                       and not FENCE.match(lines[i])):
                    content += " " + lines[i].strip()
                    i += 1
                depth = min(indent // 2, 3)
                bullet = "•" if marker in "-*+" else f"{marker}"
                st = ParagraphStyle(f"li{depth}", parent=styles["li"],
                                    leftIndent=12 + depth * 14, bulletIndent=depth * 14)
                flow.append(Paragraph(render_inline(content), st, bulletText=bullet))
            flow.append(Spacer(1, 4))
            continue

        # Block-quote
        mbq = BLOCKQUOTE.match(line)
        if mbq:
            flush_para()
            q = [mbq.group(1)]
            i += 1
            while i < n and BLOCKQUOTE.match(lines[i]):
                q.append(BLOCKQUOTE.match(lines[i]).group(1))
                i += 1
            flow.append(Paragraph(render_inline(" ".join(q).strip()), styles["quote"]))
            flow.append(Spacer(1, 4))
            continue

        # Default: paragraph text
        para.append(line)
        i += 1

    flush_para()
    return flow


# ─────────────────────────────────────────────────────────────────────────────
# PDF assembly with footer
# ─────────────────────────────────────────────────────────────────────────────
def make_footer(canvas, doc):
    canvas.saveState()
    w, _ = PAGE_SIZE
    canvas.setStrokeColor(colors.HexColor("#cccccc"))
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, FOOTER_Y + 10, w - MARGIN, FOOTER_Y + 10)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawString(MARGIN, FOOTER_Y, COMPANY_NAME)
    canvas.drawCentredString(w / 2.0, FOOTER_Y, f"Page {doc.page}")
    canvas.drawRightString(w - MARGIN, FOOTER_Y, SYSTEM_NAME)
    canvas.restoreState()


def build_pdf(flowables: list, out_path: str, title: str):
    doc = BaseDocTemplate(
        out_path, pagesize=PAGE_SIZE,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN + 8 * mm,
        title=title, author=COMPANY_NAME,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="withFooter", frames=[frame],
                                       onPage=make_footer)])
    doc.build(flowables)


# ─────────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────────
def convert_all(src_root: str, out_root: str, vol: str | None = None) -> tuple[int, int]:
    """Convert every .md under src_root to a mirrored PDF under out_root.

    Idempotent: an existing PDF at the target path is overwritten in place, so the
    script can be re-run any time to refresh docs from the maintained Markdown.

    vol: if given (e.g. "01"), only Markdown inside the top-level volume folder
         whose name starts with "<vol>_" is converted.
    """
    styles = build_styles()
    avail_w = PAGE_SIZE[0] - 2 * MARGIN
    ok, fail = 0, 0
    out_root_abs = os.path.abspath(out_root)

    for dirpath, dirnames, filenames in os.walk(src_root):
        # never descend into the output tree or hidden dirs
        if os.path.abspath(dirpath).startswith(out_root_abs):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        for fn in sorted(filenames):
            if not fn.lower().endswith(".md"):
                continue
            src_path = os.path.join(dirpath, fn)
            rel = os.path.relpath(src_path, src_root)

            # --vol filter: restrict to the "<vol>_..." top-level folder.
            if vol:
                top = rel.replace("\\", "/").split("/")[0]
                if not top.startswith(vol + "_"):
                    continue

            out_path = os.path.join(out_root, os.path.splitext(rel)[0] + ".pdf")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            try:
                with open(src_path, encoding="utf-8") as f:
                    md = f.read()
                flow = parse_markdown(md, os.path.dirname(src_path), avail_w, styles)
                if not flow:
                    flow = [Paragraph("(empty document)", styles["body"])]
                build_pdf(flow, out_path, os.path.splitext(fn)[0])   # overwrites if present
                print(f"  OK   {rel}  ->  {os.path.relpath(out_path, out_root)}")
                ok += 1
            except PermissionError:
                print(f"  LOCKED {rel}  ->  target PDF is open/locked; close it and re-run")
                fail += 1
            except Exception:
                print(f"  FAIL {rel}")
                traceback.print_exc()
                fail += 1
    return ok, fail


def main():
    ap = argparse.ArgumentParser(description="Convert Documentation_Codex Markdown to PDF with embedded SVGs.")
    ap.add_argument("--src", default=DEFAULT_SRC, help="Source root (default: this folder).")
    ap.add_argument("--out", default=DEFAULT_OUT, help="Output root (default: ./PDFs).")
    ap.add_argument("--vol", default=None,
                    help="Only render one volume, e.g. --vol 01 (accepts 01-04 or 1-4). "
                         "Restricts to the matching NN_* top-level folder.")
    args = ap.parse_args()

    vol = None
    if args.vol is not None:
        vol = args.vol.strip().zfill(2)
        if vol not in {"01", "02", "03", "04"}:
            print(f"WARNING: --vol {args.vol!r} is outside 01-04; "
                  f"nothing will match unless a '{vol}_' folder exists.", file=sys.stderr)

    if svg2rlg is None:
        print("WARNING: svglib not installed — SVG diagrams will show as placeholders.\n"
              "         Install with:  pip install svglib\n", file=sys.stderr)

    print(f"Source: {args.src}\nOutput: {args.out}"
          + (f"\nVolume: {vol}" if vol else "") + "\n")
    ok, fail = convert_all(args.src, args.out, vol=vol)
    if ok == 0 and vol:
        print(f"(No Markdown found for volume {vol}.)")
    print(f"\nDone. {ok} PDF(s) written, {fail} failed.")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
