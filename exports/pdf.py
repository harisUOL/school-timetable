"""PDF exports (reportlab): per class, per teacher and the whole school, one timetable per page."""

from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import tables

STYLES = getSampleStyleSheet()
CELL = ParagraphStyle("cell", parent=STYLES["Normal"], fontSize=8, leading=9.5, alignment=1)
DAY = ParagraphStyle("day", parent=STYLES["Normal"], fontSize=9, leading=11, fontName="Helvetica-Bold")
HEAD = ParagraphStyle("head", parent=STYLES["Normal"], fontSize=9, leading=11, alignment=1, fontName="Helvetica-Bold")


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")


def _flowables(table, width):
    story = [
        Paragraph(_escape(table.title), STYLES["Title"]),
        Paragraph(_escape(table.subtitle), STYLES["Normal"]),
        Spacer(1, 4 * mm),
    ]
    data = [[Paragraph(_escape(h), HEAD) for h in table.header]]
    for row in table.rows:
        data.append([Paragraph(_escape(row[0]), DAY)] + [Paragraph(_escape(v), CELL) for v in row[1:]])
    recess_width = 7 * mm
    first = 24 * mm
    n_recess = len(table.recess_columns)
    other = max(len(table.header) - 1 - n_recess, 1)
    each = (width - first - n_recess * recess_width) / other
    widths = [first] + [recess_width if i in table.recess_columns else each for i in range(1, len(table.header))]
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9AA5B4")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6F5")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
    ]
    for col in table.recess_columns:
        style.append(("BACKGROUND", (col, 1), (col, -1), colors.HexColor("#EEEEEE")))
    story.append(Table(data, colWidths=widths, style=TableStyle(style), repeatRows=1))
    return story


def _document(tables_, title):
    buffer = BytesIO()
    school = tables.school_name()
    generated = timezone.localtime().strftime("%d %b %Y, %H:%M")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(doc.leftMargin, 8 * mm, f"{school} · generated {generated}")
        canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=15 * mm,
        title=title,
        author=school,
    )
    story = []
    for i, table in enumerate(tables_):
        if i:
            story.append(PageBreak())
        story.extend(_flowables(table, doc.width))
    if not story:
        story = [Paragraph("Nothing to export yet.", STYLES["Normal"])]
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def class_pdf(class_group):
    return _document([tables.class_table(class_group)], f"Class {class_group} timetable")


def teacher_pdf(teacher):
    return _document([tables.teacher_table(teacher)], f"{teacher.name} timetable")


def school_pdf():
    pages = [tables.class_table(c) for c in tables.all_classes()]
    pages += [tables.teacher_table(t) for t in tables.active_teachers()]
    pages.append(tables.workload_table())
    return _document(pages, "Whole-school timetable")
