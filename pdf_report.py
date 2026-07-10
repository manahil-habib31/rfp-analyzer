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

from checklist_items import CATEGORY_META, CATEGORY_ORDER, PRIORITY_RANK

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

    # Fit Score breakdown — visible weighted components instead of one
    # opaque number.
    breakdown = verdict.get("breakdown", {})
    if breakdown:
        story.append(Spacer(1, 8))
        labels = {
            "strategicFit": "Strategic Fit", "financialTermsFit": "Financial Terms Fit",
            "complianceReadiness": "Compliance Readiness", "riskLevel": "Risk Level (100=low risk)",
        }
        rows = [[
            Paragraph("<b>Component</b>", ss["RFPCell"]), Paragraph("<b>Score</b>", ss["RFPCell"]),
            Paragraph("<b>Weight</b>", ss["RFPCell"]), Paragraph("<b>Note</b>", ss["RFPCell"]),
        ]]
        for key, label in labels.items():
            b = breakdown.get(key)
            if b:
                rows.append([
                    Paragraph(label, ss["RFPCell"]),
                    Paragraph(f"{b['score']}/100", ss["RFPCell"]),
                    Paragraph(f"{b['weightPercent']}%", ss["RFPCell"]),
                    Paragraph(str(b.get("note", "")), ss["RFPCell"]),
                ])
        story.append(_table(rows, [1.6 * inch, 0.8 * inch, 0.8 * inch, 3.2 * inch]))
        story.append(Paragraph(
            "Compliance Readiness is computed directly from the Compliance Checklist's overall score, not separately judged by the AI.",
            ParagraphStyle(name="BreakdownNote", parent=ss["Normal"], fontSize=7.5, textColor=colors.HexColor("#999999")),
        ))

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

    # Deliverables — numbered parent/child outline (1, 1.1, 1.2 ...), sorted
    # mandatory-first then by priority, no department grouping. Each child
    # point shows its section/page reference from the RFP when available.
    deliverables = analysis.get("deliverables", []) or []
    if deliverables:
        story.append(Paragraph("Deliverables", ss["RFPSection"]))
        total_days = sum(d.get("estimatedDays") or 0 for d in deliverables)
        story.append(Paragraph(
            f"{len(deliverables)} deliverables &middot; {total_days} days estimated total",
            ParagraphStyle(name="DeliverableNote", parent=ss["Normal"], fontSize=8, textColor=colors.HexColor("#777777")),
        ))
        story.append(Spacer(1, 6))
        sorted_deliverables = sorted(
            deliverables,
            key=lambda d: (not d.get("mandatory", False), -PRIORITY_RANK.get(d.get("priority", "Medium"), 2)),
        )
        PRIORITY_HEX = {"High": "#d6453d", "Medium": "#b7791f", "Low": "#6b7280"}
        deliv_parent_style = ParagraphStyle(
            name="DelivParent", parent=ss["Normal"], fontSize=9.5, fontName="Helvetica-Bold",
            spaceBefore=8, spaceAfter=3, textColor=colors.HexColor("#1a1a2e"),
        )
        deliv_child_style = ParagraphStyle(
            name="DelivChild", parent=ss["RFPCell"], leftIndent=16, spaceAfter=2,
        )
        for i, d in enumerate(sorted_deliverables, start=1):
            kind = "Mandatory" if d.get("mandatory") else "Optional"
            days = d.get("estimatedDays")
            priority = d.get("priority", "Medium")
            p_hex = PRIORITY_HEX.get(priority, "#6b7280")
            days_str = f" &middot; {days}d" if days is not None else ""
            header = (
                f"{i}. {d.get('description', '')} "
                f'<font color="{p_hex}"><b>[{priority}]</b></font> '
                f'<font color="#777777">({kind}{days_str})</font>'
            )
            story.append(Paragraph(header, deliv_parent_style))
            points = d.get("points", []) or []
            for j, p in enumerate(points, start=1):
                point_text = p.get("point", "") if isinstance(p, dict) else str(p)
                section_ref = p.get("sectionRef") if isinstance(p, dict) else None
                page_ref = p.get("pageRef") if isinstance(p, dict) else None
                ref_bits = [r for r in (section_ref, page_ref) if r]
                ref_str = f' <font color="#999999"><i>({", ".join(ref_bits)})</i></font>' if ref_bits else ""
                story.append(Paragraph(f"<b>{i}.{j}</b> {point_text}{ref_str}", deliv_child_style))

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
