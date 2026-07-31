"""
proposal_builder.py

Phase 7 (Proposal Assembly): combines the numbered Proposal Outline
(ai_engine.generate_proposal_outline) and the drafted question responses
(ai_engine.generate_question_responses) into ONE downloadable Word document.

DESIGN NOTE — why this is two clearly separated parts, not auto-merged:
Automatically placing each generated Q&A response into its "correct" outline
subsection would need its own matching step (heuristic or AI-based) to pair
a question like "Explain your approach to 24/7 monitoring" with an outline
item like "1.3 Technical Approach". That's a reasonable future enhancement,
but risky to get right under a tight timeline — a wrong auto-placement would
be worse than no placement. Instead:
  - Part 1 gives the proposal team the exact structure to fill in.
  - Part 2 gives them the drafted content to copy into the right spots.
This is safe, correct, and still saves real time over starting from a blank
page, without pretending to be smarter than it is.

Uses python-docx (NOT the docx-js/Node tooling) because this runs inside the
Streamlit app itself (Python), generating a file per RFP on demand.
"""

from io import BytesIO
from datetime import datetime

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE

NAVY = RGBColor(0x1F, 0x38, 0x64)
GREY = RGBColor(0x66, 0x66, 0x66)


def _set_base_style(doc):
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)


def _add_heading(doc, text, level=1, color=NAVY, size=16):
    p = doc.add_heading(level=level)
    run = p.add_run(text)
    run.font.color.rgb = color
    run.font.size = Pt(size)
    return p


def build_proposal_docx(
    rfp_identifier: str,
    company_name: str,
    outline: dict,
    generated_responses: list,
    source_label: str = "",
) -> bytes:
    """
    Builds the assembled proposal Word document and returns it as raw bytes
    (ready for st.download_button).

    rfp_identifier: e.g. "26-CMS-114-IAM" (from analysis["rfpIdentifier"]).
    outline: analysis["proposalOutline"] — {"sections": [{"number","title","children":[...]}]}.
    generated_responses: list of {"question","response","basedOn"} from
        ai_engine.generate_question_responses() (may be empty — Part 2 is
        simply omitted if so).
    """
    doc = Document()
    _set_base_style(doc)

    # --- Cover ---
    doc.add_paragraph().add_run()  # top spacing
    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title_p.add_run("PROPOSAL")
    title_run.font.size = Pt(28)
    title_run.font.bold = True
    title_run.font.color.rgb = NAVY

    if rfp_identifier:
        sub_p = doc.add_paragraph()
        sub_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        sub_run = sub_p.add_run(f"In Response to: {rfp_identifier}")
        sub_run.font.size = Pt(14)
        sub_run.italic = True
        sub_run.font.color.rgb = GREY

    if source_label:
        src_p = doc.add_paragraph()
        src_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        src_run = src_p.add_run(f"Source: {source_label}")
        src_run.font.size = Pt(10)
        src_run.font.color.rgb = GREY

    company_p = doc.add_paragraph()
    company_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    company_run = company_p.add_run(f"Submitted by: {company_name}")
    company_run.font.size = Pt(12)
    company_run.font.bold = True

    date_p = doc.add_paragraph()
    date_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    date_run = date_p.add_run(datetime.now().strftime("%B %d, %Y"))
    date_run.font.size = Pt(10)
    date_run.font.color.rgb = GREY

    note_p = doc.add_paragraph()
    note_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    note_run = note_p.add_run(
        "DRAFT — prepared with AI assistance. Review and edit before submission."
    )
    note_run.font.size = Pt(9)
    note_run.italic = True
    note_run.font.color.rgb = RGBColor(0xB7, 0x79, 0x1F)

    doc.add_page_break()

    # --- Part 1: Proposal Structure (from the outline) ---
    _add_heading(doc, "Part 1 — Proposal Structure", level=1, size=18)
    doc.add_paragraph(
        "The structure below follows this RFP's own submission requirements. Fill in each "
        "subsection with the relevant content — draft answers for narrative questions are "
        "provided separately in Part 2."
    ).italic = True

    sections = (outline or {}).get("sections", [])
    if not sections:
        doc.add_paragraph("No proposal outline was generated for this RFP.")
    for section in sections:
        h = doc.add_heading(level=2)
        run = h.add_run(f"{section.get('number', '')}. {section.get('title', '')}")
        run.font.color.rgb = NAVY
        run.font.size = Pt(14)
        for child in section.get("children", []):
            cp = doc.add_paragraph(style="List Bullet")
            crun = cp.add_run(f"{child.get('number', '')} {child.get('title', '')}")
            crun.font.bold = True
            placeholder = doc.add_paragraph()
            placeholder.paragraph_format.left_indent = Inches(0.3)
            ph_run = placeholder.add_run("[Content to be added — see Part 2 for related drafted answers, if any.]")
            ph_run.italic = True
            ph_run.font.color.rgb = GREY
            ph_run.font.size = Pt(9)

    # --- Part 2: Draft Response Content (from AI Response Generation) ---
    if generated_responses:
        doc.add_page_break()
        _add_heading(doc, "Part 2 — Draft Responses to RFP Questions", level=1, size=18)
        doc.add_paragraph(
            "First-pass answers to the RFP's narrative questions, drafted from the company "
            "knowledge base. Copy the relevant content into the matching subsection in Part 1, "
            "editing as needed before submission."
        ).italic = True

        for i, r in enumerate(generated_responses, start=1):
            qp = doc.add_paragraph()
            qrun = qp.add_run(f"Q{i}. {r.get('question', '')}")
            qrun.font.bold = True
            qrun.font.size = Pt(11)
            qrun.font.color.rgb = NAVY

            ap = doc.add_paragraph()
            arun = ap.add_run(r.get("response", ""))
            arun.font.size = Pt(11)

            if r.get("basedOn"):
                bp = doc.add_paragraph()
                brun = bp.add_run(f"Based on: {r['basedOn']}")
                brun.italic = True
                brun.font.size = Pt(8)
                brun.font.color.rgb = GREY

            doc.add_paragraph()  # spacing between Q&A pairs

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
