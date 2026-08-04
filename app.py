"""
app.py

Streamlit UI: upload an RFP PDF, edit the company profile in the sidebar,
run the analysis, and review results across tabs with export to Markdown/PDF.

Follows the same "set flag, then rerun" pattern used elsewhere for Streamlit
apps: since Streamlit reruns the whole script on every widget interaction,
the analysis result is cached in st.session_state so switching tabs or
tweaking unrelated widgets doesn't re-trigger an expensive (and rate-limited)
OpenAI call.
"""

import os
import json
import base64
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

from checklist_items import CATEGORY_META, CATEGORY_ORDER, DEFAULT_COMPANY_PROFILE, PRIORITY_RANK
from pdf_reader import extract_text_from_pdf, PDFExtractionError
from doc_reader import extract_text_from_documents, DocumentExtractionError, SUPPORTED_EXTENSIONS
from ai_engine import analyze_rfp, QuotaExhaustedError, AnalysisError, generate_question_responses
from proposal_builder import build_proposal_docx
from insights import (
    get_clarification_questions, format_clarification_email,
    get_deadline_urgency, get_readiness_status,
    get_executive_summary, get_effort_rollup,
    get_sanity_warnings, get_time_saved_estimate, apply_status_overrides,
)
import time as time_module
from pdf_report import generate_pdf_report
import history_store
import profile_store
from calendar_link import build_google_calendar_link

load_dotenv()
history_store.init_db()

st.set_page_config(page_title="RFP Analyzer", page_icon="\U0001F4C4", layout="wide")

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
# Design intent: this isn't a generic SaaS dashboard — it's an audit-grade
# decision tool a security/IAM company uses to decide whether to bid on
# government/enterprise contracts. Two choices carry that:
#   1. Every number that matters (scores, dates, money, deadlines) is set in
#      a monospaced face, tabular and precise — like a terminal or an audit
#      ledger, not a marketing metric. Labels stay in Inter; data speaks in
#      JetBrains Mono. That contrast is the signature.
#   2. Cards for the verdict/checklist get a colored LEFT BORDER matching
#      GO/CONDITIONAL/NO-GO — a real status signal (like an incident
#      severity strip), not decoration. Achieved via CSS :has() so it stays
#      driven by actual content, not a class we'd have to keep in sync.
# Navy/teal match proposal_builder.py's exported Word doc so the UI and the
# deliverable read as the same product. Indigo is reserved specifically for
# AI-generated content (drafted answers, AI verdict) — distinct from teal,
# which marks deterministic/rule-based results — because that distinction
# (AI judgment vs. code-enforced policy) is a real, load-bearing idea in
# this app, not just a second accent color for variety.
# ---------------------------------------------------------------------------
dark_mode = st.session_state.get("dark_mode", False)

if dark_mode:
    _root_vars = """
    --ink: #F1F5F9;
    --sidebar-bg: #0B1120;
    --ink-soft: #18213A;
    --ink-line: #263453;
    --surface: #0B1120;
    --card: #161C2C;
    --border: #2A3348;
    --text: #CBD5E1;
    --text-muted: #8B95A8;
    --teal: #2DD4CF;
    --teal-soft: rgba(45, 212, 207, 0.12);
    --indigo: #818CF8;
    --indigo-soft: rgba(129, 140, 248, 0.14);
    --go: #34D399;
    --go-soft: rgba(52, 211, 153, 0.12);
    --warn: #FBBF24;
    --warn-soft: rgba(251, 191, 36, 0.12);
    --danger: #F87171;
    --danger-soft: rgba(248, 113, 113, 0.12);
    """
else:
    _root_vars = """
    --ink: #0B1120;
    --sidebar-bg: #0B1120;
    --ink-soft: #18213A;
    --ink-line: #263453;
    --surface: #F5F6FA;
    --card: #FFFFFF;
    --border: #E7E9F2;
    --text: #33394A;
    --text-muted: #6B7280;
    --teal: #0E7490;
    --teal-soft: rgba(14, 116, 144, 0.10);
    --indigo: #4F46E5;
    --indigo-soft: rgba(79, 70, 229, 0.10);
    --go: #059669;
    --go-soft: rgba(5, 150, 105, 0.10);
    --warn: #D97706;
    --warn-soft: rgba(217, 119, 6, 0.10);
    --danger: #DC2626;
    --danger-soft: rgba(220, 38, 38, 0.10);
    """

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

:root {
__ROOT_VARS__
}

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.stApp { background: var(--surface); }
.mono { font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; }

/* ---- Logo mark (reused in sidebar + hero) ---- */
.logo-mark {
    display: inline-flex; align-items: center; justify-content: center;
    width: 34px; height: 34px; border-radius: 9px; flex-shrink: 0;
    background: linear-gradient(135deg, var(--teal) 0%, var(--sidebar-bg) 130%);
    color: #fff; font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 14px;
    box-shadow: 0 2px 6px rgba(14, 116, 144, 0.35);
}

/* ---- Sidebar ---- */
section[data-testid="stSidebar"] {
    background: var(--sidebar-bg);
    border-right: 1px solid var(--ink-line);
}
section[data-testid="stSidebar"] * { color: #E2E8F0 !important; }
section[data-testid="stSidebar"] .stCaption, section[data-testid="stSidebar"] small { color: #8B94AC !important; }
section[data-testid="stSidebar"] hr { border-color: var(--ink-line); }
section[data-testid="stSidebar"] input, section[data-testid="stSidebar"] textarea {
    background: var(--ink-soft) !important;
    color: #F1F5F9 !important;
    border: 1px solid var(--ink-line) !important;
    border-radius: 6px !important;
}
.sidebar-brand {
    display: flex; align-items: center; gap: 10px;
    padding: 2px 0 18px 0;
    border-bottom: 1px solid var(--ink-line);
    margin-bottom: 16px;
}
.sidebar-brand .brand-title { font-size: 16.5px; font-weight: 700; color: #FFFFFF !important; letter-spacing: -0.2px; line-height: 1.25; }
.sidebar-brand .brand-sub { font-size: 11.5px; color: #8B94AC !important; margin-top: 1px; }
.sidebar-section-label {
    font-size: 11px; font-weight: 700; letter-spacing: 0.8px; color: #8B94AC;
    text-transform: uppercase; margin: 6px 0 10px 0;
}
.status-pill {
    display: inline-flex; align-items: center; gap: 7px;
    font-size: 12.5px; font-weight: 600; padding: 7px 12px; border-radius: 999px;
    background: var(--ink-soft); border: 1px solid var(--ink-line); margin-bottom: 6px;
}
.status-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.status-dot.on { background: #34D399; box-shadow: 0 0 0 3px rgba(52,211,153,0.18); }
.status-dot.off { background: #F87171; box-shadow: 0 0 0 3px rgba(248,113,113,0.18); }

/* ---- Hero ---- */
.dashboard-hero { display: flex; align-items: center; gap: 12px; padding: 4px 0 20px 0; }
.dashboard-hero .hero-title {
    font-size: 22px; font-weight: 800; color: var(--ink);
    letter-spacing: -0.4px; line-height: 1.2;
}
.dashboard-hero .hero-sub { font-size: 13.5px; color: var(--text-muted); margin-top: 1px; }

/* ---- Cards ---- */
div[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--card);
    border: 1px solid var(--border) !important;
    border-radius: 12px !important;
    box-shadow: 0 1px 2px rgba(11, 17, 32, 0.04);
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(.status-flag-go) { border-left: 4px solid var(--go) !important; }
div[data-testid="stVerticalBlockBorderWrapper"]:has(.status-flag-conditional) { border-left: 4px solid var(--warn) !important; }
div[data-testid="stVerticalBlockBorderWrapper"]:has(.status-flag-nogo) { border-left: 4px solid var(--danger) !important; }

/* ---- Verdict pill ---- */
.verdict-pill {
    display: inline-flex; align-items: center; gap: 8px;
    font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 13.5px;
    letter-spacing: 0.4px; padding: 7px 14px; border-radius: 999px; margin-bottom: 4px;
}
.verdict-pill.go { background: var(--go-soft); color: var(--go); }
.verdict-pill.conditional { background: var(--warn-soft); color: var(--warn); }
.verdict-pill.nogo { background: var(--danger-soft); color: var(--danger); }

/* ---- AI vs. rule-based badges ---- */
.badge-ai {
    display: inline-block; font-size: 10.5px; font-weight: 700; letter-spacing: 0.3px;
    padding: 2px 8px; border-radius: 5px; background: var(--indigo-soft); color: var(--indigo);
}
.badge-rule {
    display: inline-block; font-size: 10.5px; font-weight: 700; letter-spacing: 0.3px;
    padding: 2px 8px; border-radius: 5px; background: var(--teal-soft); color: var(--teal);
}

/* ---- Custom metric tiles ---- */
.metric-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 6px 0 14px 0; }
.metric-tile {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 12px 14px;
}
.metric-tile .metric-label { font-size: 11px; font-weight: 600; letter-spacing: 0.4px; color: var(--text-muted); text-transform: uppercase; }
.metric-tile .metric-value {
    font-family: 'JetBrains Mono', monospace; font-size: 20px; font-weight: 700; color: var(--ink);
    margin-top: 3px; font-variant-numeric: tabular-nums;
}
@media (max-width: 700px) {
    .metric-grid { grid-template-columns: repeat(2, 1fr); }
}

/* ---- Buttons ---- */
.stButton>button {
    border-radius: 8px; font-weight: 600; border: 1px solid var(--border);
    transition: all 0.12s ease;
}
.stButton>button[kind="primary"] {
    background: var(--teal); border: none; box-shadow: 0 1px 2px rgba(14,116,144,0.25);
}
.stButton>button[kind="primary"]:hover { background: #0891B2; transform: translateY(-1px); }
section[data-testid="stSidebar"] .stButton>button {
    background: var(--ink-soft); border: 1px solid var(--ink-line); color: #E2E8F0 !important;
}

/* ---- Tabs ---- */
.stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 2px solid var(--border); }
.stTabs [data-baseweb="tab"] {
    height: 42px; border-radius: 7px 7px 0 0; font-weight: 600; color: var(--text-muted);
}
.stTabs [aria-selected="true"] { color: var(--teal) !important; border-bottom: 2px solid var(--teal); }

/* ---- Native st.metric (still used in a couple of tabs) ---- */
div[data-testid="stMetric"] {
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 12px 16px;
}
div[data-testid="stMetricValue"] {
    color: var(--ink); font-weight: 700; font-family: 'JetBrains Mono', monospace;
}

/* ---- Expanders ---- */
/* Streamlit's expander header/body text uses its own default (light-theme)
   color regardless of our background — we painted the background dark but
   never forced the text, so text stayed dark-on-dark. Also target the
   nested summary/details/content elements defensively, in case the actual
   painted surface is a child node rather than the outer wrapper. */
div[data-testid="stExpander"],
div[data-testid="stExpander"] details,
div[data-testid="stExpander"] summary,
div[data-testid="stExpander"] div[data-testid="stExpanderDetails"] {
    background: var(--card) !important;
}
div[data-testid="stExpander"] {
    border: 1px solid var(--border) !important; border-radius: 8px !important;
}
div[data-testid="stExpander"] * { color: var(--text) !important; }

/* Sidebar expanders (Company Profile, History) sit on the permanently-dark
   sidebar, so they use the fixed sidebar tones, not var(--card) — otherwise
   they'd flip to a light-mode white box even while Dark Mode is on, which
   is exactly the washed-out "light box in a dark sidebar" bug this fixes.
   These selectors are MORE specific than the general rules above (extra
   sidebar ancestor requirement), so they always win regardless of source
   order — they don't depend on being written after the general rule. */
section[data-testid="stSidebar"] div[data-testid="stExpander"],
section[data-testid="stSidebar"] div[data-testid="stExpander"] details,
section[data-testid="stSidebar"] div[data-testid="stExpander"] summary,
section[data-testid="stSidebar"] div[data-testid="stExpander"] div[data-testid="stExpanderDetails"] {
    background: var(--ink-soft) !important;
}
section[data-testid="stSidebar"] div[data-testid="stExpander"] {
    border: 1px solid var(--ink-line) !important;
}
/* Higher specificity than both the general expander text rule above and the
   sidebar's blanket text rule below, so this always wins on its own merit —
   not dependent on which one happens to be written later in the file. */
section[data-testid="stSidebar"] div[data-testid="stExpander"] * { color: #E2E8F0 !important; }

/* ---- Misc ---- */
hr { margin: 1.2rem 0; }
</style>
""".replace("__ROOT_VARS__", _root_vars), unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
if "analysis" not in st.session_state:
    st.session_state.analysis = None  # the single combined analysis result
if "source_label" not in st.session_state:
    st.session_state.source_label = None  # display label, e.g. "RFP_Main.pdf + 2 more"
if "source_docs" not in st.session_state:
    st.session_state.source_docs = []  # list of individual document names that went into it
if "generated_responses" not in st.session_state:
    st.session_state.generated_responses = None  # list of {"question","response","basedOn"} for the current analysis
if "analyzed_rfp_text" not in st.session_state:
    st.session_state.analyzed_rfp_text = ""  # the exact RFP text behind the current analysis (for on-demand calls)
if "status_overrides" not in st.session_state:
    st.session_state.status_overrides = {}  # {item_name: "GO"|"NO-GO"|"REVIEW"} — human corrections to the AI's checklist
if "analysis_duration" not in st.session_state:
    st.session_state.analysis_duration = None  # seconds the last analyze_rfp() call took, for the time-saved comparison
if "company_profile" not in st.session_state:
    st.session_state.company_profile = profile_store.load_profile()
if "current_history_id" not in st.session_state:
    st.session_state.current_history_id = None  # which history_store row the active analysis was saved as
if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False
if "doc_bytes" not in st.session_state:
    st.session_state.doc_bytes = {}  # {filename: raw_bytes} for the currently-loaded RFP's source files, used by the reference viewer

STATUS_BADGE = {"GO": "\U0001F7E2 GO", "NO-GO": "\U0001F534 NO-GO", "REVIEW": "\u26AA REVIEW"}
TAG_BADGE = {"GO": "\U0001F7E2 GO", "CONDITIONAL": "\U0001F7E1 CONDITIONAL", "NO-GO": "\U0001F534 NO-GO"}
SEVERITY_BADGE = {"HIGH": "\U0001F534 HIGH", "MEDIUM": "\U0001F7E1 MEDIUM", "LOW": "\u26AA LOW"}


# ---------------------------------------------------------------------------
# Reference viewer — lets a person click a docRef/pageRef citation anywhere
# in the analysis and jump straight to that source page, instead of only
# reading "(RFP_Exhibit_A.pdf, Page 3)" as inert text and having to go dig
# through the original file by hand.
#
# HOW THE CLICK WORKS: a real st.button (not an HTML <a href> link). An
# earlier version used a plain link with query params, which caused an
# actual browser page navigation/reload on click — and reloading the page
# always clears whatever file was sitting in the uploader (browsers never
# preserve <input type="file"> selections across a reload, framework-
# independent) and can reset Streamlit's session state entirely. A button
# click, by contrast, is a normal Streamlit rerun on the SAME session/
# WebSocket connection — st.session_state.doc_bytes stays intact.
#
# WHY page-jump is PDF-only: browsers' built-in PDF viewers understand the
# "#page=N" URL fragment natively — plain text/CSV/Word files have no
# equivalent standard. For those, the modal instead shows that document's
# full extracted text, since there's no meaningful "page" to jump to.
#
# LIMITATION (documented, not a bug): source bytes are only kept for the RFP
# currently loaded this session (st.session_state.doc_bytes) — they are NOT
# saved into history_store.py (same reasoning as analyzed_rfp_text: keeping
# every uploaded file's raw bytes in the history database indefinitely would
# bloat it for little benefit). Viewing a reference from a reloaded History
# entry will say so plainly rather than silently showing nothing.
# ---------------------------------------------------------------------------
def _get_doc_text(doc_name: str):
    """Recovers one document's extracted text from the already-combined RFP
    text for the CURRENT analysis, by splitting on the same
    "--- Document: X ---" marker doc_reader.py inserts — avoids needing a
    second copy of each document's text stored separately."""
    full_text = st.session_state.get("analyzed_rfp_text", "") or ""
    marker = f"--- Document: {doc_name} ---\n"
    if marker not in full_text:
        return None
    start = full_text.index(marker) + len(marker)
    next_marker_pos = full_text.find("--- Document: ", start)
    return full_text[start:next_marker_pos].strip() if next_marker_pos != -1 else full_text[start:].strip()


@st.dialog("\U0001F4C4 Source Reference", width="large")
def _show_reference_dialog(doc_name: str, page_ref: str):
    st.caption(f"**{doc_name}**" + (f"  ·  {page_ref}" if page_ref else ""))
    raw = st.session_state.get("doc_bytes", {}).get(doc_name)

    if raw and doc_name.lower().endswith(".pdf"):
        b64 = base64.b64encode(raw).decode()
        page_num = "".join(ch for ch in (page_ref or "") if ch.isdigit()) or "1"
        st.markdown(
            f'<iframe src="data:application/pdf;base64,{b64}#page={page_num}" '
            f'width="100%" height="600" style="border:1px solid var(--border); border-radius:8px;"></iframe>',
            unsafe_allow_html=True,
        )
    elif raw:
        st.info("Page-level jump only works for PDFs — showing this document's full extracted text instead.")
        st.text_area("Extracted text", value=_get_doc_text(doc_name) or "(text unavailable)", height=500, disabled=True)
    else:
        st.warning(
            "The original file isn't available to view — this happens when an analysis was "
            "reloaded from History (only the analysis result is saved, not the source file). "
            "Re-upload this RFP in the main uploader to view its source pages."
        )


def _ref_caption(doc_ref, section_ref=None, page_ref=None) -> str:
    """Plain (non-clickable) citation text, e.g. '(RFP.pdf, Page 3)' — used
    inline within the big HTML card strings, where a real st.button can't
    live. The actual clickable "View source" button is rendered separately,
    right after the card, via _ref_button() below."""
    bits = [r for r in (doc_ref, section_ref, page_ref) if r]
    if not bits:
        return ""
    return f" <span style='color:#94A3B8; font-size:11px; font-style:italic;'>({', '.join(bits)})</span>"


def _ref_button(label: str, doc_ref, page_ref, key: str):
    """A real Streamlit button that opens the reference dialog — stays on
    the same session (see module-level note above on why this replaced the
    earlier <a href> approach). Renders nothing if there's no doc_ref."""
    if not doc_ref:
        return
    if st.button(f"\U0001F4C4 {label}", key=key):
        _show_reference_dialog(doc_ref, page_ref)


def _flatten_html(html: str) -> str:
    """Strips per-line leading/trailing whitespace from a multi-line HTML
    template, joining with single spaces. REQUIRED before passing any
    multi-line, Python-indented f-string HTML to st.markdown(): Streamlit's
    markdown renderer (built on the same rules as the Python-Markdown
    library) treats 4-or-more leading spaces on a line as an INDENTED CODE
    BLOCK — it silently HTML-escapes that content and displays the raw tags
    as literal text instead of rendering them, rather than raising any kind
    of error. Writing an HTML template with the same indentation as the
    surrounding Python code (for readability in the .py file) is exactly
    what triggers this. This is safe to apply even to templates that
    contain NESTED pre-built HTML strings (e.g. a table's rows_html spliced
    into a table_html wrapper) since it operates on the final combined
    string's lines regardless of how deep the original nesting was."""
    return " ".join(line.strip() for line in html.splitlines() if line.strip())



def build_markdown_report(analysis: dict, source_label: str) -> str:
    v = analysis.get("verdict", {}) or {}
    rfp_identifier = analysis.get("rfpIdentifier")
    lines = [
        f"# RFP Analysis Report",
    ]
    if rfp_identifier:
        lines.append(f"**RFP:** {rfp_identifier}  ")
    lines += [
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
                doc_ref = p.get("docRef") if isinstance(p, dict) else None
                section_ref = p.get("sectionRef") if isinstance(p, dict) else None
                page_ref = p.get("pageRef") if isinstance(p, dict) else None
                ref_bits = [r for r in (doc_ref, section_ref, page_ref) if r]
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
            cite_bits = [r for r in (it.get("docRef"), it.get("pageRef")) if r]
            if cite_bits:
                evidence += f" ({', '.join(cite_bits)})"
            status_display = it.get("status", "")
            if status_display == "REVIEW" and it.get("gapType"):
                status_display += f" ({it['gapType']})"
            lines.append(f"| {it.get('item','')} | {status_display} | {reason} | {evidence} |")
        lines.append("")
    questions = analysis.get("questions", []) or []
    if questions:
        lines.append("## Extracted Questions")
        lines.append("")
        for i, q in enumerate(questions, start=1):
            ref_bits = [r for r in (q.get("docRef"), q.get("sectionRef"), q.get("pageRef")) if r]
            ref_str = f" _({', '.join(ref_bits)})_" if ref_bits else ""
            lines.append(f"{i}. {q.get('question','')}{ref_str}")
        lines.append("")
    lines.append("---")
    lines.append("*Generated by RFP Analyzer · via OpenRouter (openai/gpt-4o-mini) · Verify all terms against the original RFP before submission.*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sidebar: connection + company profile
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        "<div class='sidebar-brand'>"
        "<div class='logo-mark'>RA</div>"
        "<div><div class='brand-title'>RFP Analyzer</div>"
        "<div class='brand-sub'>Go/No-Go Decision Support</div></div>"
        "</div>",
        unsafe_allow_html=True,
    )
    api_key = os.environ.get("OPENAI_API_KEY", "")
    dot_class = "on" if api_key else "off"
    status_text = "API Connected" if api_key else "Not Connected"
    st.markdown(
        f"<div class='status-pill'><span class='status-dot {dot_class}'></span>{status_text}</div>",
        unsafe_allow_html=True,
    )
    if not api_key:
        st.caption("No API key found. Add `OPENAI_API_KEY=...` to your `.env` file and restart the app.")

    new_dark_mode = st.toggle("\U0001F319 Dark mode", value=dark_mode, key="dark_mode_toggle")
    if new_dark_mode != dark_mode:
        st.session_state.dark_mode = new_dark_mode
        st.rerun()

    st.divider()
    st.markdown("<div class='sidebar-section-label'>\U0001F3E2 Company Profile</div>", unsafe_allow_html=True)
    profile = st.session_state.company_profile
    profile_saved = profile_store.is_saved()
    st.caption(
        "\u2713 Saved profile in use." if profile_saved
        else "Using code defaults — no saved profile yet."
    )
    with st.expander(f"Editing: {profile['company_name']}", expanded=False):
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

        st.write("")
        if st.button("\U0001F4BE Save profile", use_container_width=True, type="primary"):
            profile_store.save_profile(profile)
            st.toast("Company profile saved — future analyses and app restarts will use this.", icon="\u2705")
            st.rerun()

    st.divider()
    st.markdown("<div class='sidebar-section-label'>\U0001F4DC History</div>", unsafe_allow_html=True)
    history_rows = history_store.list_history()
    if not history_rows:
        st.caption("No past analyses yet — they'll show up here after you analyze an RFP.")
    else:
        st.caption(f"{len(history_rows)} saved analysis(es).")
        for row in history_rows:
            tag = row["verdict_tag"] or "—"
            score = row["verdict_score"]
            label = row["rfp_identifier"] or row["source_label"] or f"RFP #{row['id']}"
            when = row["created_at"].split("T")[0] if row["created_at"] else ""
            dot_color = {"GO": "#34D399", "CONDITIONAL": "#FBBF24", "NO-GO": "#F87171"}.get(tag, "#8B94AC")
            badge_html = f"<span style='display:inline-block; width:7px; height:7px; border-radius:50%; background:{dot_color}; margin-right:6px;'></span>"
            with st.expander(f"{label}  ·  {when}"):
                st.markdown(f"{badge_html}**{tag}**&nbsp;&nbsp;·&nbsp;&nbsp;<span class='mono'>{score}/100</span>", unsafe_allow_html=True)
                if row["submission_deadline"]:
                    st.caption(f"Deadline: {row['submission_deadline']}")
                c1, c2 = st.columns(2)
                if c1.button("Load", key=f"load_hist_{row['id']}", use_container_width=True):
                    full = history_store.get_history_entry(row["id"])
                    st.session_state.analysis = full["analysis"]
                    st.session_state.source_label = full["source_label"]
                    st.session_state.source_docs = full["source_docs"]
                    st.session_state.generated_responses = full["generated_responses"] or None
                    st.session_state.status_overrides = full["status_overrides"] or {}
                    # Not restored: analyzed_rfp_text (the raw RFP text isn't
                    # saved to history — only the analysis result is — so
                    # "Generate more responses" on a loaded entry needs a
                    # fresh upload of the same RFP first).
                    st.session_state.analyzed_rfp_text = ""
                    st.session_state.analysis_duration = None
                    st.session_state.current_history_id = row["id"]
                    st.rerun()
                if c2.button("Delete", key=f"del_hist_{row['id']}", use_container_width=True):
                    history_store.delete_history_entry(row["id"])
                    st.rerun()


# ---------------------------------------------------------------------------
# Main: upload + analyze
# ---------------------------------------------------------------------------
st.markdown(
    "<div class='dashboard-hero'>"
    "<div class='logo-mark'>RA</div>"
    "<div><div class='hero-title'>RFP Analyzer</div>"
    "<div class='hero-sub'>AI-powered Go/No-Go decision support for Request for Proposal documents.</div></div>"
    "</div>",
    unsafe_allow_html=True,
)

upload_card = st.container(border=True)
with upload_card:
    col_upload, col_sample = st.columns([3, 1])
    with col_upload:
        uploaded_files = st.file_uploader(
            "Upload this RFP's documents (PDF, Word, CSV, or TXT) — main RFP + any "
            "Exhibits/Attachments, select all of them together",
            type=SUPPORTED_EXTENSIONS, accept_multiple_files=True,
        )
    with col_sample:
        st.write("")
        st.write("")
        use_sample = st.button("Load sample RFP", use_container_width=True)

    if uploaded_files:
        st.caption(
            f"{len(uploaded_files)} document(s) selected — they'll be combined and analyzed "
            "as ONE RFP: " + ", ".join(f.name for f in uploaded_files)
        )

# Extract + combine whichever source is active this run. Re-extracting on
# every rerun is cheap (no AI call involved) — only analyze_rfp() itself is
# gated behind the button below.
pending_text = None
pending_label = None
pending_docs = []
if use_sample:
    sample_path = os.path.join(os.path.dirname(__file__), "sample_rfp.pdf")
    try:
        pending_text = extract_text_from_pdf(sample_path)
        pending_label = "sample_rfp.pdf"
        pending_docs = ["sample_rfp.pdf"]
        with open(sample_path, "rb") as fh:
            st.session_state.doc_bytes = {"sample_rfp.pdf": fh.read()}
    except PDFExtractionError as e:
        st.error(str(e))
elif uploaded_files:
    try:
        pending_text, pending_docs = extract_text_from_documents(uploaded_files)
        pending_label = pending_docs[0] if len(pending_docs) == 1 else ", ".join(pending_docs)
        # Grab the raw bytes now, before any downstream extraction consumes
        # the stream further — .getvalue() doesn't move the read position,
        # so this doesn't interfere with extract_text_from_documents() above.
        # Reset (not merge) on every new upload batch — stale bytes from a
        # PREVIOUS RFP under the same filename would show the wrong source
        # document if left around.
        st.session_state.doc_bytes = {f.name: f.getvalue() for f in uploaded_files}
    except DocumentExtractionError as e:
        st.error(str(e))

if pending_text:
    already_done = pending_docs == st.session_state.source_docs and st.session_state.analysis is not None
    if not already_done:
        st.write("")
        st.success(f"Ready to analyze: **{pending_label}** ({len(pending_text):,} characters extracted)")
        analyze_clicked = st.button("\U0001F50D Analyze RFP", type="primary")

        if analyze_clicked:
            if not api_key:
                st.error("No OpenRouter API key configured. Add OPENAI_API_KEY to your .env file and restart the app.")
            else:
                with st.spinner(f"Analyzing {len(pending_docs)} document(s) as one RFP..."):
                    try:
                        _start_time = time_module.time()
                        result = analyze_rfp(pending_text, profile, api_key, doc_names=pending_docs)
                        st.session_state.analysis_duration = time_module.time() - _start_time
                        st.session_state.analysis = result
                        st.session_state.source_label = pending_label
                        st.session_state.source_docs = pending_docs
                        st.session_state.analyzed_rfp_text = pending_text  # kept for later on-demand calls (e.g. response generation)
                        st.session_state.generated_responses = None  # stale — belonged to the previous RFP
                        st.session_state.status_overrides = {}  # stale — belonged to the previous RFP
                        st.session_state.current_history_id = history_store.save_analysis(result, pending_label, pending_docs)
                        st.success("Analysis complete.")
                    except QuotaExhaustedError as e:
                        st.error(f"Daily quota exhausted: {e}")
                    except AnalysisError as e:
                        st.error(f"Analysis failed: {e}")

analysis = st.session_state.analysis
source_label = st.session_state.source_label

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
if analysis:
    v = analysis.get("verdict", {}) or {}
    deliverables = analysis.get("deliverables", []) or []
    # Apply any human corrections on top of the AI's checklist — every
    # downstream use in this script (metrics below, the Compliance Checklist
    # tab, and the Quick Insights readiness gate) reads from this same
    # overridden list, so a correction is reflected everywhere consistently.
    compliance = apply_status_overrides(analysis.get("compliance", []) or [], st.session_state.status_overrides)
    gaps = sum(1 for c in compliance if c.get("status") == "NO-GO")
    met = sum(1 for c in compliance if c.get("status") == "GO")
    total_days = sum(d.get("estimatedDays") or 0 for d in deliverables)

    st.divider()

    rfp_identifier = analysis.get("rfpIdentifier")

    # --- Card 1: Verdict + score metrics (the headline decision) ---
    with st.container(border=True):
        tag = v.get("tag", "—")
        score = v.get("score", "—")

        # Hidden marker read by the CSS :has() rule above to color this
        # card's left border by verdict — a real status signal, driven by
        # the actual tag value, not a class we'd have to keep manually in
        # sync with the banner text below.
        flag_class = {"GO": "status-flag-go", "CONDITIONAL": "status-flag-conditional", "NO-GO": "status-flag-nogo"}.get(tag, "")
        st.markdown(f"<span class='{flag_class}' style='display:none;'></span>", unsafe_allow_html=True)

        if rfp_identifier:
            st.markdown(f"<div style='font-size:12.5px; color:var(--text-muted); font-weight:600; letter-spacing:0.2px; margin-bottom:6px;'>\U0001F4CB {rfp_identifier}</div>", unsafe_allow_html=True)

        pill_class = {"GO": "go", "CONDITIONAL": "conditional", "NO-GO": "nogo"}.get(tag, "go")
        pill_icon = {"GO": "\u2713", "CONDITIONAL": "\u26A0", "NO-GO": "\u2715"}.get(tag, "")
        banner_msg = {
            "GO": "This opportunity looks solid — worth pursuing.",
            "CONDITIONAL": "Proceed with caution — one or more items need resolution before committing.",
            "NO-GO": "This opportunity fails a hard requirement — recommend not pursuing as-is.",
        }.get(tag, "")
        st.markdown(
            f"<div class='verdict-pill {pill_class}'>{pill_icon} {tag}"
            f"<span style='opacity:0.6; font-weight:500;'>&nbsp;·&nbsp;{score}/100</span></div>"
            f"<div style='font-size:13.5px; color:var(--text-muted); margin-bottom:14px;'>{banner_msg}</div>",
            unsafe_allow_html=True,
        )

        if analysis.get("extractionWarnings"):
            st.warning(
                "\u26A0\uFE0F " + "\n\n".join(analysis["extractionWarnings"])
            )

        if analysis.get("complianceWarnings"):
            st.warning(
                "Some parts of the compliance checklist couldn't be retrieved and are marked "
                "REVIEW below — worth checking manually:\n\n"
                + "\n".join(f"- {w}" for w in analysis["complianceWarnings"])
            )

        st.markdown(
            "<div class='metric-grid'>"
            f"<div class='metric-tile'><div class='metric-label'>Fit Score</div>"
            f"<div class='metric-value mono'>{score}/100</div></div>"
            f"<div class='metric-tile'><div class='metric-label'>Verdict</div>"
            f"<div class='metric-value mono' style='font-size:16px;'>{TAG_BADGE.get(tag, tag)}</div></div>"
            f"<div class='metric-tile'><div class='metric-label'>Deliverables / Est. Days</div>"
            f"<div class='metric-value mono'>{len(deliverables)} / {total_days}</div></div>"
            f"<div class='metric-tile'><div class='metric-label'>GO Items / NO-GO Items</div>"
            f"<div class='metric-value mono'>{met} / {gaps}</div></div>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.info(v.get("summary", ""))

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

    # -----------------------------------------------------------------------
    # Card 2: Quick Insights — zero extra API cost, computed entirely from
    # data the analysis already produced (see insights.py). No LLM call
    # involved.
    # -----------------------------------------------------------------------
    with st.container(border=True):
        st.markdown(
            "<div style='display:flex; align-items:center; gap:8px; margin-bottom:2px;'>"
            "<span style='font-weight:700; font-size:15px;'>\u26A1 Quick Insights</span>"
            "<span class='badge-rule'>RULE-BASED</span></div>",
            unsafe_allow_html=True,
        )
        st.caption(get_executive_summary(analysis))

        kdb_top = analysis.get("keyDatesBudget", {}) or {}
        urgency = get_deadline_urgency(kdb_top.get("submissionDeadlineISO"))
        readiness = get_readiness_status(compliance)

        cal_link = build_google_calendar_link(
            title=f"RFP Submission Deadline: {rfp_identifier or 'Untitled RFP'}",
            date_iso=kdb_top.get("submissionDeadlineISO"),
            details=f"Submission deadline for {rfp_identifier or 'this RFP'}, extracted by RFP Analyzer.",
        )
        cal_button_html = (
            f"<a href='{cal_link}' target='_blank' rel='noopener' style='display:block; text-align:center; "
            f"margin-top:10px; padding:7px 10px; border-radius:7px; border:1px solid var(--border); "
            f"background:var(--card); color:var(--teal); font-size:12.5px; font-weight:600; "
            f"text-decoration:none;'>\U0001F4C5 Add to Google Calendar</a>"
        ) if cal_link else ""

        # Single grid block (not st.columns) so both boxes share one row and
        # stretch to equal height automatically — st.columns renders each
        # side as an independent block, so mismatched text length (a
        # 1-line deadline vs. a 2-line readiness message) left them visibly
        # uneven. The calendar link is now plain HTML *inside* box 1 instead
        # of a separate st.link_button call, which removes the gap Streamlit
        # was leaving between the two.
        st.markdown(
            "<div style='display:grid; grid-template-columns:1fr 1fr; gap:12px; align-items:stretch;'>"
            f"<div style='padding:12px; border-radius:8px; background:{urgency['color']}1F; "
            f"border:1px solid {urgency['color']}55; display:flex; flex-direction:column; justify-content:space-between;'>"
            f"<div><b style='color:var(--text);'>Submission Deadline</b><br>"
            f"<span style='color:var(--text-muted); font-size:13.5px;'>{urgency['label']}</span></div>"
            f"{cal_button_html}"
            "</div>"
            f"<div style='padding:12px; border-radius:8px; background:{readiness['color']}1F; "
            f"border:1px solid {readiness['color']}55;'>"
            f"<b style='color:var(--text);'>Submission Readiness</b><br>"
            f"<span style='color:var(--text-muted); font-size:13.5px;'>{readiness['label']}</span></div>"
            "</div>",
            unsafe_allow_html=True,
        )

        st.caption(get_effort_rollup(analysis))

        clarification_qs = get_clarification_questions(compliance)
        if clarification_qs:
            with st.expander(f"\U0001F4E7 {len(clarification_qs)} question(s) to ask the client before submitting"):
                for q in clarification_qs:
                    st.markdown(f"- **{q['item']}**: {q['question']}")
                email_text = format_clarification_email(clarification_qs, analysis.get("rfpIdentifier", ""))
                st.text_area("Copy-ready email", value=email_text, height=200, key="clarification_email_box")

        sanity_warnings = get_sanity_warnings(analysis)
        if sanity_warnings:
            with st.expander(f"\U0001F50D {len(sanity_warnings)} data quality flag(s) — worth a quick check", expanded=False):
                st.caption("Automatic sanity checks on the extracted data — not AI-judged, just arithmetic/date checks.")
                for w in sanity_warnings:
                    st.markdown(f"- {w}")

        st.caption(f"\u23f1\ufe0f {get_time_saved_estimate(analysis, st.session_state.analysis_duration)}")

    st.write("")
    tabs = st.tabs([
        "\U0001F4E6 Deliverables", "\U0001F4CA Evaluation Criteria",
        "\U0001F9FE Compliance Checklist", "\U0001F4C5 Dates & Budget",
        "\u2696\uFE0F Strengths & Risks", "\U0001F5C2\uFE0F Proposal Outline",
        "\u2753 Extracted Questions",
    ])

    with tabs[0]:
        PRIORITY_COLOR = {"High": "#DC2626", "Medium": "#D97706", "Low": "var(--text-muted)"}
        if not deliverables:
            st.caption("No deliverables extracted.")
        sorted_deliverables = sorted(
            deliverables,
            key=lambda d: PRIORITY_RANK.get(d.get("priority", "Medium"), 2),
            reverse=True,
        )
        for i, d in enumerate(sorted_deliverables, start=1):
            mandatory = d.get("mandatory")
            kind_label = "Mandatory" if mandatory else "Optional"
            kind_color = "#DC2626" if mandatory else "var(--text-muted)"
            days = d.get("estimatedDays")
            priority = d.get("priority", "Medium")
            pc = PRIORITY_COLOR.get(priority, "var(--text-muted)")

            points_html = ""
            points_with_refs = []  # (label, doc_ref, page_ref) — rendered as real buttons after the card
            for j, p in enumerate(d.get("points", []) or [], start=1):
                point_text = p.get("point", "") if isinstance(p, dict) else str(p)
                doc_ref = p.get("docRef") if isinstance(p, dict) else None
                section_ref = p.get("sectionRef") if isinstance(p, dict) else None
                page_ref = p.get("pageRef") if isinstance(p, dict) else None
                ref_str = _ref_caption(doc_ref, section_ref, page_ref)
                if doc_ref:
                    points_with_refs.append((f"{i}.{j}", doc_ref, page_ref))
                points_html += (
                    f"<div style='padding:4px 0; font-size:13.5px; color:var(--text);'>"
                    f"<b style='color:var(--ink);'>{i}.{j}</b>&nbsp; {point_text}{ref_str}</div>"
                )

            st.markdown(
                _flatten_html(f"""
                <div style="background:var(--card); border:1px solid var(--border); border-radius:10px;
                            padding:16px 18px; margin-bottom:12px; box-shadow:0 1px 3px rgba(11,17,32,0.04);">
                    <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:10px; margin-bottom:8px;">
                        <div style="font-weight:700; font-size:15px; color:var(--ink);">{i}. {d.get('description','')}</div>
                    </div>
                    <div style="display:flex; gap:6px; flex-wrap:wrap; margin-bottom:10px;">
                        <span style="font-size:11px; font-weight:700; color:{pc}; background:{pc}18; padding:3px 10px; border-radius:20px;">{priority.upper()}</span>
                        <span style="font-size:11px; font-weight:700; color:{kind_color}; background:{kind_color}18; padding:3px 10px; border-radius:20px;">{kind_label.upper()}</span>
                        {f"<span style='font-size:11px; font-weight:700; color:#0E7490; background:#0E749018; padding:3px 10px; border-radius:20px;' class='mono'>&#9201; {days}d</span>" if days is not None else ""}
                    </div>
                    {points_html}
                </div>
                """),
                unsafe_allow_html=True,
            )
            if points_with_refs:
                chunk_size = 4
                for start in range(0, len(points_with_refs), chunk_size):
                    chunk = points_with_refs[start:start + chunk_size]
                    ref_cols = st.columns(chunk_size)
                    for col, (label, doc_ref, page_ref) in zip(ref_cols, chunk):
                        with col:
                            _ref_button(label, doc_ref, page_ref, key=f"refbtn_deliv_{i}_{label}")

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
            rec_color = {"Proceed": "#16A34A", "Review Needed": "#D97706", "High Risk": "#DC2626"}.get(overall.get("recommendation"), "var(--text-muted)")
            st.markdown(_flatten_html(f"""
            <div style="background:var(--card); border:1px solid var(--border); border-radius:10px; padding:16px 18px; margin-bottom:14px; box-shadow:0 1px 3px rgba(11,17,32,0.04);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                    <span style="font-weight:700; font-size:13px; color:var(--text-muted); letter-spacing:0.4px;">OVERALL COMPLIANCE</span>
                    <span style="font-weight:800; font-size:22px; color:var(--ink); font-family:'JetBrains Mono',monospace;">{overall.get('score','—')}%</span>
                    <span style="background:{rec_color}18; color:{rec_color}; padding:3px 12px; border-radius:20px; font-size:12px; font-weight:700;">{overall.get('recommendation','')}</span>
                </div>
                <div style="font-size:12.5px; color:var(--text-muted);">{overall.get('summary','')}</div>
            </div>
            """), unsafe_allow_html=True)
            cols = st.columns(len(dept_scores.get("byCategory", {})) or 1)
            for i, (cat, s) in enumerate(dept_scores.get("byCategory", {}).items()):
                rc = {"Proceed": "#16A34A", "Review Needed": "#D97706", "High Risk": "#DC2626"}.get(s.get("recommendation"), "var(--text-muted)")
                with cols[i]:
                    st.markdown(_flatten_html(f"""
                    <div style="background:var(--card); border:1px solid var(--border); border-radius:10px; padding:12px 10px; text-align:center; box-shadow:0 1px 3px rgba(11,17,32,0.04);">
                        <div style="font-size:11px; color:#94A3B8; text-transform:uppercase; font-weight:600; letter-spacing:0.4px;">{s.get('title','')}</div>
                        <div style="font-size:24px; font-weight:800; color:var(--ink); font-family:'JetBrains Mono',monospace;">{s.get('score','—')}%</div>
                        <div style="font-size:11px; color:{rc}; font-weight:700;">{s.get('recommendation','')}</div>
                    </div>
                    """), unsafe_allow_html=True)
            st.caption("Scores are computed directly from the checklist below (GO=100, REVIEW=50, NO-GO=0, averaged per department) — not separately judged by the AI, so they're always consistent with the detailed results.")

        STATUS_COLOR = {"GO": "#16A34A", "NO-GO": "#DC2626", "REVIEW": "#7C3AED"}

        filter_choice = st.radio(
            "Show", ["All", "GO", "NO-GO", "REVIEW"], horizontal=True, key="checklist_status_filter",
        )

        for cat in CATEGORY_ORDER:
            cat_items_all = [c for c in compliance if c.get("category") == cat]
            cat_items = cat_items_all if filter_choice == "All" else [c for c in cat_items_all if c.get("status") == filter_choice]
            if not cat_items_all:
                continue
            meta = CATEGORY_META[cat]
            with st.expander(f"{meta['emoji']} {meta['title']} ({len(cat_items)} of {len(cat_items_all)} shown)", expanded=True):
                if not cat_items:
                    st.caption(f"No items with status '{filter_choice}' in this category.")
                rows_html = ""
                items_with_refs = []  # (item_name, doc_ref, page_ref) — rendered as real buttons after the table
                for it in cat_items:
                    status = it.get("status", "REVIEW")
                    color = STATUS_COLOR.get(status, "#7C3AED")
                    gap_type = it.get("gapType")
                    status_badge = f"<span style=\"background:{color}18; color:{color}; padding:3px 10px; border-radius:20px; font-size:11px; font-weight:700;\">{status}</span>"
                    if status == "REVIEW" and gap_type:
                        status_badge += f"<br><span style='font-size:10px; color:#94A3B8; font-style:italic;'>{gap_type}</span>"
                    if it.get("overridden"):
                        status_badge += "<br><span style='font-size:9px; color:#2563EB; font-weight:700;'>\u270f\uFE0F MANUALLY OVERRIDDEN</span>"
                    evidence = it.get("evidence") or "<span style='color:#94A3B8;'>Not cited in RFP</span>"
                    evidence += _ref_caption(it.get("docRef"), page_ref=it.get("pageRef"))
                    if it.get("docRef"):
                        items_with_refs.append((it.get("item", "?"), it.get("docRef"), it.get("pageRef")))
                    rows_html += f"""
                    <tr style="border-bottom:1px solid var(--border);">
                        <td style="padding:10px 8px; vertical-align:top; font-weight:600; color:var(--ink); width:18%;">{it.get('item','')}</td>
                        <td style="padding:10px 8px; vertical-align:top; width:10%;">
                            {status_badge}
                        </td>
                        <td style="padding:10px 8px; vertical-align:top; width:32%; font-size:13px; color:var(--text);">{it.get('reason','')}</td>
                        <td style="padding:10px 8px; vertical-align:top; width:40%; font-size:12.5px; color:var(--text-muted);">{evidence}</td>
                    </tr>"""
                table_html = f"""
                <table style="width:100%; border-collapse:collapse;">
                    <thead>
                        <tr style="border-bottom:2px solid var(--border); text-align:left; font-size:11px; text-transform:uppercase; color:#94A3B8; letter-spacing:0.4px;">
                            <th style="padding:8px;">Checklist Item</th>
                            <th style="padding:8px;">Decision</th>
                            <th style="padding:8px;">Reason</th>
                            <th style="padding:8px;">Evidence from RFP</th>
                        </tr>
                    </thead>
                    <tbody>{rows_html}</tbody>
                </table>"""
                st.markdown(_flatten_html(table_html), unsafe_allow_html=True)

                if items_with_refs:
                    st.caption("View source:")
                    chunk_size = 4
                    for start in range(0, len(items_with_refs), chunk_size):
                        chunk = items_with_refs[start:start + chunk_size]
                        ref_cols = st.columns(chunk_size)
                        for col, (item_name, doc_ref, page_ref) in zip(ref_cols, chunk):
                            with col:
                                _ref_button(item_name, doc_ref, page_ref, key=f"refbtn_comp_{cat}_{item_name}")

                # Human override — pick an item in THIS category and correct its
                # status. Every downstream view (this tab, Quick Insights, exports)
                # reads the overridden list, so a correction is reflected everywhere.
                item_names = [it.get("item") for it in cat_items_all]
                oc1, oc2, oc3 = st.columns([2, 1, 1])
                with oc1:
                    override_item = st.selectbox(
                        "Correct an item", item_names, key=f"override_item_{cat}", label_visibility="collapsed",
                    )
                with oc2:
                    override_to = st.selectbox(
                        "New status", ["GO", "NO-GO", "REVIEW"], key=f"override_to_{cat}", label_visibility="collapsed",
                    )
                with oc3:
                    if st.button("Apply", key=f"override_apply_{cat}", use_container_width=True):
                        st.session_state.status_overrides[override_item] = override_to
                        if st.session_state.current_history_id:
                            history_store.update_status_overrides(
                                st.session_state.current_history_id, st.session_state.status_overrides
                            )
                        st.rerun()

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
            st.markdown("<div style='font-weight:700; font-size:15px; color:var(--ink); margin-bottom:8px;'>\u2705 Strengths</div>", unsafe_allow_html=True)
            if not strengths:
                st.caption("No strengths returned.")
            for s in strengths:
                st.markdown(f"**{s.get('point','')}**")
                st.caption(s.get("note", ""))
        with col_r:
            st.markdown("<div style='font-weight:700; font-size:15px; color:var(--ink); margin-bottom:8px;'>\u26A0\uFE0F Risks / Weaknesses</div>", unsafe_allow_html=True)
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

        if sections:
            st.divider()
            st.caption(
                "Assembles this outline plus any drafted Q&A responses (see the "
                "Extracted Questions tab) into one downloadable Word document — "
                "structure in Part 1, draft answers to copy in from Part 2."
            )
            proposal_docx = build_proposal_docx(
                rfp_identifier=analysis.get("rfpIdentifier") or "",
                company_name=profile.get("company_name", "SPS"),
                outline=outline,
                generated_responses=st.session_state.generated_responses or [],
                source_label=source_label or "",
                deliverables=analysis.get("deliverables", []),
                dates_budget=analysis.get("keyDatesBudget", {}),
            )
            proposal_safe_name = "".join(
                c if c.isalnum() or c in "-_" else "_"
                for c in (analysis.get("rfpIdentifier") or source_label or "RFP")
            ).strip("_") or "RFP"
            st.download_button(
                "\U0001F4C4 Download Proposal Draft (Word)",
                data=proposal_docx,
                file_name=f"{proposal_safe_name}_proposal_draft.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
            if not st.session_state.generated_responses:
                st.caption(
                    "\u2139\ufe0f No AI-drafted answers yet — Part 2 will be empty. Generate them "
                    "from the Extracted Questions tab first if this RFP has narrative questions."
                )

    with tabs[6]:
        questions = analysis.get("questions", []) or []
        if not questions:
            st.caption("No direct questions were found in this RFP (this is normal — many RFPs "
                       "only use the fixed compliance checklist, with no open-ended prose questions).")
        else:
            st.caption(
                f"{len(questions)} direct question(s) the RFP asks the vendor to answer in prose "
                "— separate from the fixed compliance checklist."
            )
            for i, q in enumerate(questions, start=1):
                ref_str = _ref_caption(q.get("docRef"), q.get("sectionRef"), q.get("pageRef"))
                st.markdown(f"**Q{i}.** {q.get('question','')}{ref_str}", unsafe_allow_html=True)
                _ref_button("View source", q.get("docRef"), q.get("pageRef"), key=f"refbtn_q_{i}")

            st.divider()

            if st.session_state.generated_responses is None:
                if st.button("\u2728 Generate AI Responses", type="primary"):
                    if not api_key:
                        st.error("No OpenRouter API key configured. Add OPENAI_API_KEY to your .env file and restart the app.")
                    else:
                        with st.spinner("Drafting responses from the company knowledge base..."):
                            try:
                                st.session_state.generated_responses = generate_question_responses(
                                    st.session_state.get("analyzed_rfp_text", ""), questions, api_key
                                )
                                if st.session_state.current_history_id:
                                    history_store.update_generated_responses(
                                        st.session_state.current_history_id, st.session_state.generated_responses
                                    )
                                st.rerun()
                            except QuotaExhaustedError as e:
                                st.error(f"Daily quota exhausted: {e}")
                            except AnalysisError as e:
                                st.error(f"Response generation failed: {e}")
            else:
                st.markdown(
                    "<div style='display:flex; align-items:center; gap:8px; margin-bottom:2px;'>"
                    "<span style='font-weight:700; font-size:15px;'>\U0001F4DD Draft Responses (from company knowledge base)</span>"
                    "<span class='badge-ai'>AI-GENERATED</span></div>",
                    unsafe_allow_html=True,
                )
                st.caption(
                    "First-pass answers grounded in knowledge_base.py — review and edit before "
                    "using in an actual submission."
                )
                for i, r in enumerate(st.session_state.generated_responses, start=1):
                    st.markdown(f"**Q{i}. {r.get('question','')}**")
                    st.markdown(r.get("response", ""))
                    if r.get("basedOn"):
                        st.caption(f"Based on: {r['basedOn']}")
                    st.markdown("")
                if st.button("\U0001F504 Regenerate"):
                    st.session_state.generated_responses = None
                    st.rerun()

    st.divider()
    rfp_identifier = analysis.get("rfpIdentifier")
    if rfp_identifier:
        filename_base = rfp_identifier
    else:
        # Fallback for the rare case the AI couldn't find any number/title in the text.
        docs = st.session_state.source_docs or [source_label or "RFP"]
        filename_base = docs[0].rsplit(".", 1)[0] if docs else "RFP"
        if len(docs) > 1:
            filename_base += f"_plus{len(docs) - 1}docs"
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in filename_base).strip("_") or "RFP"
    dl1, dl2 = st.columns(2)
    md_report = build_markdown_report(analysis, source_label or "RFP")
    dl1.download_button(
        "\u2b07\ufe0f Download Markdown report", data=md_report,
        file_name=f"{safe_name}_analysis_report.md", mime="text/markdown", use_container_width=True,
    )
    pdf_bytes = generate_pdf_report(analysis, source_label or "RFP")
    dl2.download_button(
        "\u2b07\ufe0f Download PDF report", data=pdf_bytes,
        file_name=f"{safe_name}_analysis_report.pdf", mime="application/pdf", use_container_width=True,
    )
else:
    st.caption("Upload this RFP's document(s) — main RFP plus any Exhibits/Attachments — (or load the sample), then click Analyze.")
