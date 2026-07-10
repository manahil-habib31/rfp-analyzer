"""
app.py

Streamlit UI: upload an RFP PDF, edit the company profile in the sidebar,
run the analysis, and review results across tabs with export to Markdown/PDF.

Follows the same "set flag, then rerun" pattern used elsewhere for Streamlit
apps: since Streamlit reruns the whole script on every widget interaction,
the analysis result is cached in st.session_state so switching tabs or
tweaking unrelated widgets doesn't re-trigger an expensive (and rate-limited)
Gemini call.
"""

import os
import json
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

from checklist_items import CATEGORY_META, CATEGORY_ORDER, DEFAULT_COMPANY_PROFILE, PRIORITY_RANK
from pdf_reader import extract_text_from_pdf, PDFExtractionError
from ai_engine import analyze_rfp, QuotaExhaustedError, AnalysisError
from pdf_report import generate_pdf_report

load_dotenv()

st.set_page_config(page_title="RFP Analyzer", page_icon="\U0001F4C4", layout="wide")

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
if "analysis" not in st.session_state:
    st.session_state.analysis = None
if "source_label" not in st.session_state:
    st.session_state.source_label = None
if "company_profile" not in st.session_state:
    st.session_state.company_profile = dict(DEFAULT_COMPANY_PROFILE)

STATUS_BADGE = {"GO": "\U0001F7E2 GO", "NO-GO": "\U0001F534 NO-GO", "REVIEW": "\u26AA REVIEW"}
TAG_BADGE = {"GO": "\U0001F7E2 GO", "CONDITIONAL": "\U0001F7E1 CONDITIONAL", "NO-GO": "\U0001F534 NO-GO"}
SEVERITY_BADGE = {"HIGH": "\U0001F534 HIGH", "MEDIUM": "\U0001F7E1 MEDIUM", "LOW": "\u26AA LOW"}


def build_markdown_report(analysis: dict, source_label: str) -> str:
    v = analysis.get("verdict", {}) or {}
    lines = [
        f"# RFP Analysis Report",
        f"**Source:** {source_label}  ",
        f"**Generated:** {datetime.now().strftime('%B %d, %Y')}",
        "",
        f"## Verdict: {TAG_BADGE.get(v.get('tag'), v.get('tag',''))} — Fit score {v.get('score','—')}/100",
        v.get("summary", ""),
        "",
    ]
    breakdown = v.get("breakdown", {})
    if breakdown:
        lines.append("### Score Breakdown")
        lines.append("")
        lines.append("| Component | Score | Weight | Note |")
        lines.append("|---|---|---|---|")
        labels = {
            "strategicFit": "Strategic Fit", "financialTermsFit": "Financial Terms Fit",
            "complianceReadiness": "Compliance Readiness", "riskLevel": "Risk Level (100=low risk)",
        }
        for key, label in labels.items():
            b = breakdown.get(key)
            if b:
                note = (b.get("note", "") or "").replace("|", "/")
                lines.append(f"| {label} | {b['score']}/100 | {b['weightPercent']}% | {note} |")
        lines.append("")
    dept_scores = analysis.get("departmentScores", {})
    if dept_scores:
        overall = dept_scores.get("overall", {})
        lines.append("## Compliance Evaluation Scores")
        lines.append("")
        lines.append("| Department / Category | Score | Recommendation | Summary |")
        lines.append("|---|---|---|---|")
        lines.append(f"| **OVERALL COMPLIANCE** | {overall.get('score','—')}% | {overall.get('recommendation','')} | {overall.get('summary','')} |")
        for cat in CATEGORY_ORDER:
            s = dept_scores.get("byCategory", {}).get(cat)
            if s:
                lines.append(f"| {s.get('title',cat)} | {s.get('score','—')}% | {s.get('recommendation','')} | {s.get('summary','')} |")
        lines.append("")
    deliverables = analysis.get("deliverables", []) or []
    if deliverables:
        lines.append("## Deliverables")
        lines.append("")
        for i, d in enumerate(deliverables, start=1):
            kind = "Mandatory" if d.get("mandatory") else "Optional"
            days = d.get("estimatedDays")
            days_str = f" ({days}d)" if days is not None else ""
            priority = d.get("priority", "Medium")
            lines.append(f"### {i}. {d.get('description','')} — [{kind}, {priority}]{days_str}")
            points = d.get("points", []) or []
            for j, p in enumerate(points, start=1):
                point_text = p.get("point", "") if isinstance(p, dict) else str(p)
                section_ref = p.get("sectionRef") if isinstance(p, dict) else None
                page_ref = p.get("pageRef") if isinstance(p, dict) else None
                ref_bits = [r for r in (section_ref, page_ref) if r]
                ref_str = f" _({', '.join(ref_bits)})_" if ref_bits else ""
                lines.append(f"- **{i}.{j}** {point_text}{ref_str}")
            lines.append("")
    criteria = analysis.get("evaluationCriteria", []) or []
    if criteria:
        lines.append("## Evaluation Criteria")
        for c in criteria:
            w = c.get("weightPercent")
            lines.append(f"- {c.get('criterion','')}" + (f" — {w}%" if w is not None else ""))
        lines.append("")
    kdb = analysis.get("keyDatesBudget", {}) or {}
    if kdb:
        lines.append("## Key Dates & Budget")
        lines.append(f"- Submission deadline: {kdb.get('submissionDeadline') or '—'}")
        lines.append(f"- Contract value: {'$' + format(kdb['contractValueUSD'], ',.0f') if kdb.get('contractValueUSD') is not None else '—'}")
        lines.append(f"- Payment terms: {'NET ' + str(kdb['paymentTermsDays']) if kdb.get('paymentTermsDays') is not None else '—'}")
        lines.append(f"- Insurance required: {'$' + format(kdb['insuranceAmountUSD'], ',.0f') if kdb.get('insuranceAmountUSD') is not None else '—'}")
        lines.append(f"- Bond required: {kdb.get('bondDetails') if kdb.get('bondRequired') else ('No' if kdb.get('bondRequired') is False else '—')}")
        lines.append("")
    strengths = analysis.get("strengths", []) or []
    if strengths:
        lines.append("## Strengths")
        for s in strengths:
            lines.append(f"- **{s.get('point','')}** — {s.get('note','')}")
        lines.append("")
    risks = analysis.get("risks", []) or []
    if risks:
        lines.append("## Risks / Weaknesses")
        for r in risks:
            lines.append(f"- **[{r.get('severity','')}]** {r.get('risk','')} — {r.get('note','')}")
        lines.append("")
    compliance = analysis.get("compliance", []) or []
    for cat in CATEGORY_ORDER:
        items = [c for c in compliance if c.get("category") == cat]
        if not items:
            continue
        lines.append(f"## {CATEGORY_META[cat]['title']} Checklist")
        lines.append("")
        lines.append("| Item | Status | Reason | Evidence from RFP |")
        lines.append("|---|---|---|---|")
        for it in items:
            reason = (it.get("reason", "") or "").replace("|", "/")
            evidence = (it.get("evidence") or "Not cited in RFP").replace("|", "/")
            if it.get("pageRef"):
                evidence += f" ({it['pageRef']})"
            lines.append(f"| {it.get('item','')} | {it.get('status','')} | {reason} | {evidence} |")
        lines.append("")
    lines.append("---")
    lines.append("*Generated by RFP Analyzer · Gemini 2.5 Flash · Verify all terms against the original RFP before submission.*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sidebar: connection + company profile
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("\U0001F511 Connection")
    
    env_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
    api_key = st.text_input(
        "Gemini API key", value=env_key, type="password",
        help="Get a free key at aistudio.google.com. Loaded from .env if set there.",
    )
    if not api_key:
        st.caption("\u26A0\uFE0F No API key set — add one above or in a `.env` file (`GEMINI_API_KEY=...`).")

    st.divider()
    st.header("\U0001F3E2 Company Profile")
    st.caption("Used to judge fit — edit these to match the company actually bidding.")
    profile = st.session_state.company_profile
    profile["company_name"] = st.text_input("Company name", profile["company_name"])
    profile["services"] = st.text_area("Services / capabilities", profile["services"], height=70)
    profile["years_experience"] = st.number_input("Years of relevant experience", min_value=0, value=int(profile["years_experience"]))
    profile["max_insurance_available_usd"] = st.number_input(
        "Max insurance coverage available (USD)", min_value=0,
        value=int(profile["max_insurance_available_usd"]), step=500000)
    profile["acceptable_payment_terms_days"] = st.number_input(
        "Acceptable payment terms (days)", min_value=0,
        value=int(profile["acceptable_payment_terms_days"]))
    profile["certifications"] = st.text_input("Certifications", profile["certifications"])
    profile["annual_revenue_usd"] = st.number_input(
        "Annual revenue (USD)", min_value=0,
        value=int(profile["annual_revenue_usd"]), step=500000)
    profile["can_provide_audited_financials"] = st.checkbox(
        "Can provide audited financial statements", value=profile["can_provide_audited_financials"])
    profile["registered_states"] = st.text_input("State registration status", profile["registered_states"])


# ---------------------------------------------------------------------------
# Main: upload + analyze
# ---------------------------------------------------------------------------
st.title("\U0001F4C4 RFP Analyzer")
st.caption("AI-powered Go/No-Go decision support for Request for Proposal documents.")

col_upload, col_sample = st.columns([3, 1])
with col_upload:
    uploaded_file = st.file_uploader("Upload an RFP (PDF)", type=["pdf"])
with col_sample:
    st.write("")
    st.write("")
    use_sample = st.button("Load sample RFP", use_container_width=True)

rfp_text = None
source_label = None

if use_sample:
    sample_path = os.path.join(os.path.dirname(__file__), "sample_rfp.pdf")
    try:
        rfp_text = extract_text_from_pdf(sample_path)
        source_label = "sample_rfp.pdf"
        st.session_state["_pending_text"] = rfp_text
        st.session_state["_pending_source"] = source_label
    except PDFExtractionError as e:
        st.error(str(e))
elif uploaded_file is not None:
    try:
        rfp_text = extract_text_from_pdf(uploaded_file)
        source_label = uploaded_file.name
        st.session_state["_pending_text"] = rfp_text
        st.session_state["_pending_source"] = source_label
    except PDFExtractionError as e:
        st.error(str(e))

pending_text = st.session_state.get("_pending_text")
pending_source = st.session_state.get("_pending_source")

if pending_text:
    st.success(f"Loaded: **{pending_source}** ({len(pending_text):,} characters extracted)")
    analyze_clicked = st.button("\U0001F50D Analyze RFP", type="primary")

    if analyze_clicked:
        if not api_key:
            st.error("No Gemini API key set. Add one in the sidebar first.")
        else:
            with st.spinner("Analyzing against the checklist and company profile..."):
                try:
                    result = analyze_rfp(pending_text, profile, api_key)
                    st.session_state.analysis = result
                    st.session_state.source_label = pending_source
                    st.success("Analysis complete.")
                except QuotaExhaustedError as e:
                    st.error(f"Daily quota exhausted: {e}")
                except AnalysisError as e:
                    st.error(f"Analysis failed: {e}")

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
analysis = st.session_state.analysis
if analysis:
    v = analysis.get("verdict", {}) or {}
    deliverables = analysis.get("deliverables", []) or []
    compliance = analysis.get("compliance", []) or []
    gaps = sum(1 for c in compliance if c.get("status") == "NO-GO")
    met = sum(1 for c in compliance if c.get("status") == "GO")
    total_days = sum(d.get("estimatedDays") or 0 for d in deliverables)

    st.divider()

    tag = v.get("tag", "—")
    score = v.get("score", "—")
    banner_fn = {"GO": st.success, "CONDITIONAL": st.warning, "NO-GO": st.error}.get(tag, st.info)
    banner_msg = {
        "GO": "This opportunity looks solid — worth pursuing.",
        "CONDITIONAL": "Proceed with caution — one or more items need resolution before committing.",
        "NO-GO": "This opportunity fails a hard requirement — recommend not pursuing as-is.",
    }.get(tag, "")
    banner_fn(f"**RECOMMENDATION: {tag}** (Score: {score}/100)  \n{banner_msg}")

    breakdown = v.get("breakdown", {})
    if breakdown:
        with st.expander("Why this score? (weighted breakdown)", expanded=False):
            labels = {
                "strategicFit": "Strategic Fit",
                "financialTermsFit": "Financial Terms Fit",
                "complianceReadiness": "Compliance Readiness",
                "riskLevel": "Risk Level (100 = low risk)",
            }
            for key, label in labels.items():
                b = breakdown.get(key)
                if not b:
                    continue
                st.markdown(f"**{label}** — {b['score']}/100 &nbsp; _(weight: {b['weightPercent']}%)_", unsafe_allow_html=True)
                st.progress(b["score"] / 100)
                st.caption(b["note"])
            st.caption(
                "Compliance Readiness is computed directly from the Compliance Checklist tab's "
                "overall score — it is never separately judged by the AI, so it can't contradict "
                "the detailed checklist results."
            )

    if analysis.get("complianceWarnings"):
        st.warning(
            "Some parts of the compliance checklist couldn't be retrieved and are marked "
            "REVIEW below — worth checking manually:\n\n"
            + "\n".join(f"- {w}" for w in analysis["complianceWarnings"])
        )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Fit score", f"{score}/100")
    with m2:
        st.caption("Verdict")
        tag_color = {"GO": "green", "CONDITIONAL": "orange", "NO-GO": "red"}.get(tag, "gray")
        st.markdown(f":{tag_color}[**{TAG_BADGE.get(tag, tag)}**]")
    m3.metric("Deliverables / est. days", f"{len(deliverables)} / {total_days}")
    m4.metric("GO items / NO-GO items", f"{met} / {gaps}")
    st.info(v.get("summary", ""))

    tabs = st.tabs([
        "\U0001F4E6 Deliverables", "\U0001F4CA Evaluation Criteria",
        "\U0001F9FE Compliance Checklist", "\U0001F4C5 Dates & Budget",
        "\u2696\uFE0F Strengths & Risks", "\U0001F5C2\uFE0F Proposal Outline",
    ])

    with tabs[0]:
        PRIORITY_COLOR = {"High": "#d6453d", "Medium": "#b7791f", "Low": "#6b7280"}
        if not deliverables:
            st.caption("No deliverables extracted.")
        sorted_deliverables = sorted(
            deliverables,
            key=lambda d: PRIORITY_RANK.get(d.get("priority", "Medium"), 2),
            reverse=True,
        )
        for i, d in enumerate(sorted_deliverables, start=1):
            kind = "\U0001F534 Mandatory" if d.get("mandatory") else "\u26AA Optional"
            days = d.get("estimatedDays")
            priority = d.get("priority", "Medium")
            pc = PRIORITY_COLOR.get(priority, "#6b7280")
            st.markdown(
                f"#### {i}. {d.get('description','')} "
                + (f"<span style='font-size:12px; color:#888;'>&middot; \u23f1\ufe0f {days}d</span> " if days is not None else "")
                + f"<span style='font-size:11px; font-weight:700; color:{pc}; background:{pc}22; padding:2px 8px; border-radius:5px;'>{priority}</span> "
                + f"<span style='font-size:11px; color:#888;'>&nbsp;{kind}</span>",
                unsafe_allow_html=True,
            )
            points = d.get("points", []) or []
            for j, p in enumerate(points, start=1):
                point_text = p.get("point", "") if isinstance(p, dict) else str(p)
                section_ref = p.get("sectionRef") if isinstance(p, dict) else None
                page_ref = p.get("pageRef") if isinstance(p, dict) else None
                ref_bits = [r for r in (section_ref, page_ref) if r]
                ref_str = f" <span style='color:#888; font-size:11px; font-style:italic;'>({', '.join(ref_bits)})</span>" if ref_bits else ""
                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;**{i}.{j}** {point_text}{ref_str}", unsafe_allow_html=True)
            st.markdown("")

    with tabs[1]:
        criteria = analysis.get("evaluationCriteria", []) or []
        for c in criteria:
            w = c.get("weightPercent")
            st.markdown(f"**{c.get('criterion','')}**")
            st.progress(min(100, w or 0) / 100, text=f"{w}%" if w is not None else "weight not stated")

    with tabs[2]:
        dept_scores = analysis.get("departmentScores", {})
        if dept_scores:
            overall = dept_scores.get("overall", {})
            rec_color = {"Proceed": "#1f9d6b", "Review Needed": "#b7791f", "High Risk": "#d6453d"}.get(overall.get("recommendation"), "#888")
            st.markdown(f"""
            <div style="border:1px solid #333; border-radius:8px; padding:14px 16px; margin-bottom:14px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                    <span style="font-weight:700; font-size:14px;">OVERALL COMPLIANCE</span>
                    <span style="font-weight:700; font-size:20px;">{overall.get('score','—')}%</span>
                    <span style="background:{rec_color}22; color:{rec_color}; padding:3px 10px; border-radius:5px; font-size:12px; font-weight:700;">{overall.get('recommendation','')}</span>
                </div>
                <div style="font-size:12.5px; color:#aaa;">{overall.get('summary','')}</div>
            </div>
            """, unsafe_allow_html=True)
            cols = st.columns(len(dept_scores.get("byCategory", {})) or 1)
            for i, (cat, s) in enumerate(dept_scores.get("byCategory", {}).items()):
                rc = {"Proceed": "#1f9d6b", "Review Needed": "#b7791f", "High Risk": "#d6453d"}.get(s.get("recommendation"), "#888")
                with cols[i]:
                    st.markdown(f"""
                    <div style="border:1px solid #333; border-radius:8px; padding:10px 12px; text-align:center;">
                        <div style="font-size:11px; color:#888; text-transform:uppercase;">{s.get('title','')}</div>
                        <div style="font-size:22px; font-weight:700;">{s.get('score','—')}%</div>
                        <div style="font-size:11px; color:{rc}; font-weight:700;">{s.get('recommendation','')}</div>
                    </div>
                    """, unsafe_allow_html=True)
            st.caption("Scores are computed directly from the checklist below (GO=100, REVIEW=50, NO-GO=0, averaged per department) — not separately judged by the AI, so they're always consistent with the detailed results.")

        STATUS_COLOR = {"GO": "#1f9d6b", "NO-GO": "#d6453d", "REVIEW": "#8881a3"}
        for cat in CATEGORY_ORDER:
            cat_items = [c for c in compliance if c.get("category") == cat]
            if not cat_items:
                continue
            meta = CATEGORY_META[cat]
            with st.expander(f"{meta['emoji']} {meta['title']} ({len(cat_items)} items)", expanded=True):
                rows_html = ""
                for it in cat_items:
                    status = it.get("status", "REVIEW")
                    color = STATUS_COLOR.get(status, "#8881a3")
                    evidence = it.get("evidence") or "<span style='color:#888;'>Not cited in RFP</span>"
                    page_ref = it.get("pageRef")
                    if page_ref:
                        evidence += f" <span style='color:#666; font-style:italic;'>({page_ref})</span>"
                    rows_html += f"""
                    <tr style="border-bottom:1px solid #333;">
                        <td style="padding:8px; vertical-align:top; font-weight:600; width:18%;">{it.get('item','')}</td>
                        <td style="padding:8px; vertical-align:top; width:10%;">
                            <span style="background:{color}22; color:{color}; padding:2px 8px; border-radius:5px; font-size:11px; font-weight:700;">{status}</span>
                        </td>
                        <td style="padding:8px; vertical-align:top; width:32%; font-size:13px;">{it.get('reason','')}</td>
                        <td style="padding:8px; vertical-align:top; width:40%; font-size:12.5px; color:#aaa;">{evidence}</td>
                    </tr>"""
                table_html = f"""
                <table style="width:100%; border-collapse:collapse;">
                    <thead>
                        <tr style="border-bottom:2px solid #444; text-align:left; font-size:11px; text-transform:uppercase; color:#888;">
                            <th style="padding:8px;">Checklist Item</th>
                            <th style="padding:8px;">Decision</th>
                            <th style="padding:8px;">Reason</th>
                            <th style="padding:8px;">Evidence from RFP</th>
                        </tr>
                    </thead>
                    <tbody>{rows_html}</tbody>
                </table>"""
                st.markdown(table_html, unsafe_allow_html=True)

    with tabs[3]:
        kdb = analysis.get("keyDatesBudget", {}) or {}
        c1, c2 = st.columns(2)
        c1.metric("Submission deadline", kdb.get("submissionDeadline") or "—")
        c1.metric("Contract value", f"${kdb['contractValueUSD']:,.0f}" if kdb.get("contractValueUSD") is not None else "—")
        c2.metric("Payment terms", f"NET {kdb['paymentTermsDays']}" if kdb.get("paymentTermsDays") is not None else "—")
        c2.metric("Insurance required", f"${kdb['insuranceAmountUSD']:,.0f}" if kdb.get("insuranceAmountUSD") is not None else "—")
        if kdb.get("bondRequired"):
            st.warning(f"Bond required: {kdb.get('bondDetails','')}")
        elif kdb.get("bondRequired") is False:
            st.success("No bond required.")

    with tabs[4]:
        strengths = analysis.get("strengths", []) or []
        risks = analysis.get("risks", []) or []
        col_s, col_r = st.columns(2)
        with col_s:
            st.markdown("#### \u2705 Strengths")
            if not strengths:
                st.caption("No strengths returned.")
            for s in strengths:
                st.markdown(f"**{s.get('point','')}**")
                st.caption(s.get("note", ""))
        with col_r:
            st.markdown("#### \u26A0\uFE0F Risks / Weaknesses")
            if not risks:
                st.caption("No risks returned.")
            for r in risks:
                st.markdown(f"**{SEVERITY_BADGE.get(r.get('severity'), r.get('severity',''))}** — {r.get('risk','')}")
                st.caption(r.get("note", ""))

    with tabs[5]:
        if analysis.get("outlineWarning"):
            st.warning(f"Outline generation had an issue: {analysis['outlineWarning']}")
        outline = analysis.get("proposalOutline", {}) or {}
        sections = outline.get("sections", [])
        if not sections:
            st.caption("No proposal outline generated.")
        for section in sections:
            st.markdown(f"**{section.get('number','')}. {section.get('title','')}**")
            for child in section.get("children", []):
                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;{child.get('number','')} {child.get('title','')}", unsafe_allow_html=True)

    st.divider()
    dl1, dl2 = st.columns(2)
    md_report = build_markdown_report(analysis, st.session_state.source_label or "RFP")
    dl1.download_button(
        "\u2b07\ufe0f Download Markdown report", data=md_report,
        file_name="rfp_analysis_report.md", mime="text/markdown", use_container_width=True,
    )
    pdf_bytes = generate_pdf_report(analysis, st.session_state.source_label or "RFP")
    dl2.download_button(
        "\u2b07\ufe0f Download PDF report", data=pdf_bytes,
        file_name="rfp_analysis_report.pdf", mime="application/pdf", use_container_width=True,
    )
else:
    st.caption("Upload an RFP or load the sample, then click Analyze RFP.")
