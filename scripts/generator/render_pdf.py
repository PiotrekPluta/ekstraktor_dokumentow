"""PDF rendering via reportlab, with reproducible output and Polish glyphs.

DATA_SPEC.md R0.3 requires fixed ``/CreationDate``, ``/ModDate`` and ``/ID`` —
reportlab's ``invariant=1`` mode exists for exactly this purpose (verified:
two runs of an identical story produce byte-identical PDFs).
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .models import Block

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
_FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]

FONT_NAME = "DejaVuSans"
_FONT_BOLD_NAME = "DejaVuSans-Bold"
_registered = False


def ensure_font_registered() -> None:
    global _registered
    if _registered:
        return
    regular = next((p for p in _FONT_CANDIDATES if Path(p).exists()), None)
    bold = next((p for p in _FONT_BOLD_CANDIDATES if Path(p).exists()), None)
    if regular is None:
        raise RuntimeError(
            "DejaVu Sans font not found; install the 'fonts-dejavu-core' package "
            "(see docs/PROJECT_NOTES.md section 6)."
        )
    pdfmetrics.registerFont(TTFont(FONT_NAME, regular))
    if bold:
        pdfmetrics.registerFont(TTFont(_FONT_BOLD_NAME, bold))
    _registered = True


def _styles() -> dict:
    ensure_font_registered()
    base = getSampleStyleSheet()
    bold_name = _FONT_BOLD_NAME if pdfmetrics.getRegisteredFontNames().count(_FONT_BOLD_NAME) else FONT_NAME
    return {
        "normal": ParagraphStyle("normal", parent=base["Normal"], fontName=FONT_NAME, fontSize=10, leading=13),
        "normal_bold": ParagraphStyle("normal_bold", parent=base["Normal"], fontName=bold_name, fontSize=10, leading=13),
        "normal_right": ParagraphStyle("normal_right", parent=base["Normal"], fontName=FONT_NAME, fontSize=10, leading=13, alignment=TA_RIGHT),
        "small": ParagraphStyle("small", parent=base["Normal"], fontName=FONT_NAME, fontSize=7, leading=9),
        "small_right": ParagraphStyle("small_right", parent=base["Normal"], fontName=FONT_NAME, fontSize=7, leading=9, alignment=TA_RIGHT),
        "heading": ParagraphStyle("heading", parent=base["Heading2"], fontName=bold_name, fontSize=14, leading=18, alignment=TA_CENTER),
    }


def _flowables(blocks: list[Block]):
    styles = _styles()
    flow = []
    for b in blocks:
        if b.kind == "heading":
            flow.append(Paragraph(b.text, styles["heading"]))
        elif b.kind == "paragraph":
            if b.size == "small":
                style = styles["small_right"] if b.align == "right" else styles["small"]
            else:
                style = styles["normal_bold"] if b.bold else styles["normal"]
                if b.align == "right":
                    style = styles["normal_right"]
            flow.append(Paragraph(b.text.replace("\n", "<br/>"), style))
        elif b.kind == "table":
            t = Table(b.rows, hAlign="LEFT")
            t.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), FONT_NAME),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("GRID", (0, 0), (-1, -1), 0.5, "#666666"),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                )
            )
            flow.append(t)
        elif b.kind == "spacer":
            flow.append(Spacer(1, 10))
        elif b.kind == "page_break":
            flow.append(PageBreak())
        elif b.kind == "raw_html":
            flow.append(Paragraph(b.raw_html, styles["small"]))
    return flow


def render_pdf(blocks: list[Block], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        invariant=1,
        topMargin=48,
        bottomMargin=48,
        leftMargin=48,
        rightMargin=48,
    )
    doc.build(_flowables(blocks))
