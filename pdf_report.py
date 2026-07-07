"""
pdf_report.py

Renders a completed RFP analysis into a styled, downloadable PDF using
ReportLab's Platypus layer (Table/Paragraph flowables rather than raw canvas
drawing, so text wraps properly across variable-length reasons).
"""

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, ListFlowable,
    ListItem, HRFlowable,
)
from reportlab.lib.enums import TA_CENTER

from checklist_items import CATEGORY_META, CATEGORY_ORDER

TONE_COLORS = {
    "GO": colors.HexColor("#1f9d6b"),
    "CONDITIONAL": colors.HexColor("#b7791f"),
    "NO-GO": colors.HexColor("#d6453d"),
}
TONE_HEX = {"GO": "#1f9d6b", "CONDITIONAL": "#b7791f", "NO-GO": "#d6453d"}
STATUS_COLORS = {
    "GO": colors.HexColor("#1f9d6b"),
    "NO-GO": colors.HexColor("#d6453d"),
    "REVIEW": colors.HexColor("#6b7280"),
}
STATUS_HEX = {"GO": "#1f9d6b", "NO-GO": "#d6453d", "REVIEW": "#6b7280"}
SEVERITY_COLORS = {
    "HIGH": colors.HexColor("#d6453d"),
    "MEDIUM": colors.HexColor("#b7791f"),
    "LOW": colors.HexColor("#6b7280"),
}
SEVERITY_HEX = {"HIGH": "#d6453d", "MEDIUM": "#b7791f", "LOW": "#6b7280"}


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle(name="RFPTitle", parent=ss["Title"], fontSize=20, spaceAfter=2))
    ss.add(ParagraphStyle(name="RFPMeta", parent=ss["Normal"], textColor=colors.HexColor("#777777"), fontSize=9, spaceAfter=14))
    ss.add(ParagraphStyle(name="RFPSection", parent=ss["Heading2"], fontSize=13, spaceBefore=18, spaceAfter=8, textColor=colors.HexColor("#1a1a2e")))
    ss.add(ParagraphStyle(name="RFPCell", parent=ss["Normal"], fontSize=8.5, leading=11))
    ss.add(ParagraphStyle(name="RFPCellBold", parent=ss["Normal"], fontSize=8.5, leading=11, fontName="Helvetica-Bold"))
    ss.add(ParagraphStyle(name="ScoreBig", parent=ss["Normal"], fontSize=30, fontName="Helvetica-Bold", alignment=TA_CENTER))
    ss.add(ParagraphStyle(name="ScoreLabel", parent=ss["Normal"], fontSize=7, alignment=TA_CENTER, textColor=colors.HexColor("#999999")))
    return ss


def _table(rows, col_widths, header=True):
    t = Table(rows, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e3e3e3")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f7")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
        ]
    t.setStyle(TableStyle(style))
    return t


def generate_pdf_report(analysis: dict, source_label: str) -> bytes:
    """Builds the PDF and returns it as raw bytes (ready to hand to
    st.download_button or write to disk)."""
    ss = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
    )
    story = []

    verdict = analysis.get("verdict", {}) or {}
    tag = verdict.get("tag", "REVIEW")
    tone = TONE_COLORS.get(tag, colors.grey)

    story.append(Paragraph("RFP Analysis Report", ss["RFPTitle"]))
    story.append(Paragraph(f"Source: {source_label}", ss["RFPMeta"]))

    score_cell = [
        Paragraph(str(verdict.get("score", "\u2014")), ParagraphStyle(
            name="ScoreDyn", parent=ss["ScoreBig"], textColor=tone)),
        Paragraph("FIT SCORE", ss["ScoreLabel"]),
    ]
    summary_cell = [
        Paragraph(f'<font color="{TONE_HEX.get(tag, "#666666")}"><b>{tag}</b></font>', ss["Normal"]),
        Spacer(1, 4),
        Paragraph(verdict.get("summary", ""), ss["Normal"]),
    ]
    verdict_table = Table([[score_cell, summary_cell]], colWidths=[1.1 * inch, 5.7 * inch])
    verdict_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(verdict_table)

    # Department compliance scorecard
    dept_scores = analysis.get("departmentScores", {})
    if dept_scores:
        story.append(Spacer(1, 10))
        overall = dept_scores.get("overall", {})
        rows = [[
            Paragraph("<b>Department / Category</b>", ss["RFPCell"]),
            Paragraph("<b>Score</b>", ss["RFPCell"]),
            Paragraph("<b>Recommendation</b>", ss["RFPCell"]),
            Paragraph("<b>Summary</b>", ss["RFPCell"]),
        ]]
        rec_hex = {"Proceed": "#1f9d6b", "Review Needed": "#b7791f", "High Risk": "#d6453d"}
        def score_row(title, s, bold=False):
            rc = rec_hex.get(s.get("recommendation"), "#888888")
            name_style = ss["RFPCellBold"] if bold else ss["RFPCell"]
            return [
                Paragraph(title, name_style),
                Paragraph(f"{s.get('score','—')}%", name_style),
                Paragraph(f'<font color="{rc}"><b>{s.get("recommendation","")}</b></font>', ss["RFPCell"]),
                Paragraph(s.get("summary", ""), ss["RFPCell"]),
            ]
        rows.append(score_row("OVERALL COMPLIANCE", overall, bold=True))
        for cat in CATEGORY_ORDER:
            s = dept_scores.get("byCategory", {}).get(cat)
            if s:
                rows.append(score_row(s.get("title", cat), s))
        story.append(_table(rows, [1.5 * inch, 0.7 * inch, 1.1 * inch, 3.1 * inch]))
        story.append(Paragraph(
            "Scores are computed directly from the checklist below (GO=100, REVIEW=50, NO-GO=0, averaged per department), not separately judged by the AI.",
            ParagraphStyle(name="ScoreNote", parent=ss["Normal"], fontSize=7.5, textColor=colors.HexColor("#999999")),
        ))

    # Deliverables
    deliverables = analysis.get("deliverables", []) or []
    if deliverables:
        story.append(Paragraph("Deliverables", ss["RFPSection"]))
        rows = [[Paragraph("<b>Deliverable</b>", ss["RFPCell"]), Paragraph("<b>Type</b>", ss["RFPCell"]), Paragraph("<b>Est. weeks</b>", ss["RFPCell"])]]
        for d in deliverables:
            kind = "Mandatory" if d.get("mandatory") else "Optional"
            weeks = d.get("effortEstimateWeeks")
            rows.append([
                Paragraph(str(d.get("description", "")), ss["RFPCell"]),
                Paragraph(kind, ss["RFPCell"]),
                Paragraph(str(weeks) if weeks is not None else "\u2014", ss["RFPCell"]),
            ])
        story.append(_table(rows, [4.2 * inch, 1.5 * inch, 1.1 * inch]))

    # Evaluation criteria
    criteria = analysis.get("evaluationCriteria", []) or []
    if criteria:
        story.append(Paragraph("Evaluation Criteria", ss["RFPSection"]))
        rows = [[Paragraph("<b>Criterion</b>", ss["RFPCell"]), Paragraph("<b>Weight</b>", ss["RFPCell"])]]
        for c in criteria:
            w = c.get("weightPercent")
            rows.append([
                Paragraph(str(c.get("criterion", "")), ss["RFPCell"]),
                Paragraph(f"{w}%" if w is not None else "\u2014", ss["RFPCell"]),
            ])
        story.append(_table(rows, [5.8 * inch, 1.0 * inch]))

    # Key dates & budget
    kdb = analysis.get("keyDatesBudget", {}) or {}
    if kdb:
        story.append(Paragraph("Key Dates &amp; Budget", ss["RFPSection"]))
        def fmt_money(v):
            return f"${v:,.0f}" if isinstance(v, (int, float)) else "\u2014"
        rows = [
            [Paragraph("<b>Submission deadline</b>", ss["RFPCell"]), Paragraph(str(kdb.get("submissionDeadline") or "\u2014"), ss["RFPCell"])],
            [Paragraph("<b>Contract value</b>", ss["RFPCell"]), Paragraph(fmt_money(kdb.get("contractValueUSD")), ss["RFPCell"])],
            [Paragraph("<b>Payment terms</b>", ss["RFPCell"]), Paragraph(f"NET {kdb['paymentTermsDays']}" if kdb.get("paymentTermsDays") is not None else "\u2014", ss["RFPCell"])],
            [Paragraph("<b>Insurance required</b>", ss["RFPCell"]), Paragraph(fmt_money(kdb.get("insuranceAmountUSD")), ss["RFPCell"])],
            [Paragraph("<b>Bond required</b>", ss["RFPCell"]), Paragraph(str(kdb.get("bondDetails")) if kdb.get("bondRequired") else ("No" if kdb.get("bondRequired") is False else "\u2014"), ss["RFPCell"])],
        ]
        story.append(_table(rows, [2.0 * inch, 4.8 * inch], header=False))

    # Strengths
    strengths = analysis.get("strengths", []) or []
    if strengths:
        story.append(Paragraph("Strengths", ss["RFPSection"]))
        rows = [[Paragraph("<b>Strength</b>", ss["RFPCell"]), Paragraph("<b>Note</b>", ss["RFPCell"])]]
        for s in strengths:
            rows.append([
                Paragraph(f"<b>{s.get('point','')}</b>", ss["RFPCell"]),
                Paragraph(str(s.get("note", "")), ss["RFPCell"]),
            ])
        story.append(_table(rows, [2.2 * inch, 4.6 * inch]))

    # Risk assessment
    risks = analysis.get("risks", []) or []
    if risks:
        story.append(Paragraph("Risks / Weaknesses", ss["RFPSection"]))
        rows = [[Paragraph("<b>Risk</b>", ss["RFPCell"]), Paragraph("<b>Severity</b>", ss["RFPCell"]), Paragraph("<b>Note</b>", ss["RFPCell"])]]
        for r in risks:
            sev = r.get("severity", "MEDIUM")
            sev_hex = SEVERITY_HEX.get(sev, "#6b7280")
            rows.append([
                Paragraph(str(r.get("risk", "")), ss["RFPCell"]),
                Paragraph(f'<font color="{sev_hex}"><b>{sev}</b></font>', ss["RFPCell"]),
                Paragraph(str(r.get("note", "")), ss["RFPCell"]),
            ])
        story.append(_table(rows, [1.8 * inch, 0.9 * inch, 4.1 * inch]))

    # Compliance checklist, by department
    compliance = analysis.get("compliance", []) or []
    for cat in CATEGORY_ORDER:
        cat_items = [c for c in compliance if c.get("category") == cat]
        if not cat_items:
            continue
        meta = CATEGORY_META[cat]
        story.append(Paragraph(f"{meta['title']} Checklist", ss["RFPSection"]))
        rows = [[
            Paragraph("<b>Item</b>", ss["RFPCell"]),
            Paragraph("<b>Status</b>", ss["RFPCell"]),
            Paragraph("<b>Reason</b>", ss["RFPCell"]),
            Paragraph("<b>Evidence from RFP</b>", ss["RFPCell"]),
        ]]
        for it in cat_items:
            status = it.get("status", "REVIEW")
            status_hex = STATUS_HEX.get(status, "#6b7280")
            evidence = it.get("evidence") or '<font color="#999999">Not cited in RFP</font>'
            if it.get("pageRef"):
                evidence = f"{evidence} <font color=\"#999999\"><i>({it['pageRef']})</i></font>"
            rows.append([
                Paragraph(f"<b>{it.get('item','')}</b>", ss["RFPCell"]),
                Paragraph(f'<font color="{status_hex}"><b>{status}</b></font>', ss["RFPCell"]),
                Paragraph(str(it.get("reason", "")), ss["RFPCell"]),
                Paragraph(str(evidence), ss["RFPCell"]),
            ])
        story.append(_table(rows, [1.5 * inch, 0.7 * inch, 2.3 * inch, 1.7 * inch]))

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#eeeeee")))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Generated by RFP Analyzer \u00b7 Gemini 2.5 Flash \u00b7 Verify all terms against the original RFP before submission.",
        ParagraphStyle(name="Footer", parent=ss["Normal"], fontSize=7.5, textColor=colors.HexColor("#aaaaaa")),
    ))

    doc.build(story)
    return buf.getvalue()
