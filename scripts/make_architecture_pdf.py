"""Render docs/architecture-summary.md as a readable, font-embedded PDF.

Install the optional documentation dependency with `pip install reportlab`.
The Markdown document remains the source of truth for the PDF text.
"""

from html import escape
from pathlib import Path
import re

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "architecture-summary.md"
OUTPUT = ROOT / "docs" / "architecture-summary.pdf"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
if (FONT_DIR / "DejaVuSans.ttf").exists():
    regular = FONT_DIR / "DejaVuSans.ttf"
    bold = FONT_DIR / "DejaVuSans-Bold.ttf"
else:
    FONT_DIR = Path(reportlab.__file__).parent / "fonts"
    regular = FONT_DIR / "Vera.ttf"
    bold = FONT_DIR / "VeraBd.ttf"
pdfmetrics.registerFont(TTFont("PipelineSans", str(regular)))
pdfmetrics.registerFont(TTFont("PipelineSansBold", str(bold)))
pdfmetrics.registerFontFamily("PipelineSans", normal="PipelineSans", bold="PipelineSansBold")

NAVY = colors.HexColor("#142b35")
TEAL = colors.HexColor("#0d827b")
MUTED = colors.HexColor("#546b71")
PALE = colors.HexColor("#e9f4f1")
RULE = colors.HexColor("#d9e8e4")

styles = {
    "eyebrow": ParagraphStyle("eyebrow", fontName="PipelineSansBold", fontSize=8,
                              leading=12, textColor=TEAL, spaceAfter=9),
    "title": ParagraphStyle("title", fontName="PipelineSansBold", fontSize=25,
                            leading=31, textColor=NAVY, spaceAfter=4),
    "subtitle": ParagraphStyle("subtitle", fontName="PipelineSans", fontSize=9.5,
                               leading=14, textColor=MUTED, spaceAfter=12),
    "section": ParagraphStyle("section", fontName="PipelineSansBold", fontSize=12,
                              leading=17, textColor=NAVY, spaceBefore=13, spaceAfter=5),
    "body": ParagraphStyle("body", fontName="PipelineSans", fontSize=8.8,
                           leading=13.5, textColor=NAVY, spaceAfter=5),
    "small": ParagraphStyle("small", fontName="PipelineSans", fontSize=8,
                            leading=12, textColor=MUTED),
    "card": ParagraphStyle("card", fontName="PipelineSansBold", fontSize=8.5,
                           leading=12, textColor=NAVY, alignment=TA_LEFT),
}


def page_frame(canvas, document):
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, document.pagesize[1] - 11, document.pagesize[0], 11, fill=1, stroke=0)
    canvas.setStrokeColor(RULE)
    canvas.line(43, 39, document.pagesize[0] - 43, 39)
    canvas.setFont("PipelineSans", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(43, 27, "MINI HIRING PIPELINE  /  ARCHITECTURE SUMMARY")
    canvas.drawRightString(document.pagesize[0] - 43, 27, str(document.page))
    canvas.restoreState()


def build():
    markdown = SOURCE.read_text(encoding="utf-8")
    match = re.search(r"^Repository:\s*(https?://\S+)", markdown, re.M)
    if not match:
        raise ValueError("The architecture summary needs a repository URL.")
    repository = match.group(1)

    document = SimpleDocTemplate(
        str(OUTPUT), pagesize=(612, 792), rightMargin=43, leftMargin=43,
        topMargin=40, bottomMargin=55,
        title="Mini Hiring Pipeline - Architecture Summary", author="Diya Virmani",
    )
    story = [
        Paragraph("MINI HIRING PIPELINE", styles["eyebrow"]),
        Paragraph("Architecture summary", styles["title"]),
        Paragraph("One recruiter. One job. Auditable stage changes and read-only search.", styles["subtitle"]),
        HRFlowable(width="100%", thickness=.8, color=RULE), Spacer(1, 10),
        Paragraph(f'<b>Repository:</b> <link href="{escape(repository)}" color="#0d827b">{escape(repository)}</link>', styles["body"]),
    ]

    cards = [[Paragraph(label, styles["card"]) for label in
              ("01  Browser", "02  FastAPI", "03  Services", "04  SQLite")],
             [Paragraph(label, styles["small"]) for label in
              ("Same-origin UI and signed login", "Authenticated, validated requests",
               "Stage rules and search filters", "Profiles and immutable events")]]
    flow = Table(cards, colWidths=[131.5] * 4, hAlign="LEFT")
    flow.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 9),
        ("LINEAFTER", (0, 0), (2, -1), 4, colors.white),
    ]))
    story.extend([
        Paragraph("Request and data flow", styles["section"]), flow,
        Paragraph("Integrity and search boundary", styles["section"]),
        Paragraph(
            "SQLite triggers enforce Applied as the first event, one-step moves, rejection "
            "before Hired, terminal outcomes, and immutable history. A serialized write "
            "transaction checks the expected stage, so stale concurrent moves fail.", styles["body"]
        ),
        Paragraph(
            "The newest event determines current stage and stage duration. Search combines "
            "deterministic filters and typo-tolerant name ranking. Only unresolved queries "
            "reach Gemini; its structured filters are validated and executed read-only. "
            "AI cannot change candidates or decide transitions.", styles["body"]
        ),
    ])

    sections = re.split(r"^## (.+)$", markdown, flags=re.M)
    for index in range(1, len(sections), 2):
        title, content = sections[index], sections[index + 1]
        if title == "Request and data flow":
            continue  # The compact flow above is complemented by the other sections.
        lines = []
        for line in content.strip().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            stripped = re.sub(r"^\d+\.\s+", "", stripped)
            lines.append(stripped)
        if not lines:
            continue
        elements = [Paragraph(escape(title), styles["section"])]
        for line in lines:
            elements.append(Paragraph(escape(line), styles["body"]))
        story.append(KeepTogether(elements))

    story.append(Spacer(1, 9))
    story.append(Paragraph(
        "The Markdown file beside this PDF is its editable source. The README contains "
        "the latest test and search evaluation results.", styles["small"]
    ))
    document.build(story, onFirstPage=page_frame, onLaterPages=page_frame)
    print(OUTPUT)


if __name__ == "__main__":
    build()
