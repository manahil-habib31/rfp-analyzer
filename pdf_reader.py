"""
pdf_reader.py

Extracts plain text from an uploaded RFP PDF (or several) using pdfplumber.
Kept as its own module so the extraction strategy (and any future OCR
fallback for scanned PDFs) can change without touching the UI or the AI
engine.

A single RFP often arrives as several separate files (the main RFP document
plus Exhibit A, Exhibit B, Attachment C, etc.). extract_text_from_documents()
combines all of them into ONE piece of text for a single analysis, marking
every page with BOTH the source document name and the page number
("--- Document: RFP_Main.pdf, Page 3 ---") so the AI engine can cite exactly
which file a given deliverable/checklist item came from, not just a page
number.
"""

import pdfplumber


class PDFExtractionError(Exception):
    """Raised when a PDF can't be read or contains no extractable text."""
    pass


def _get_display_name(file_obj, fallback: str) -> str:
    """Best-effort filename for a file object — Streamlit's UploadedFile has
    a .name attribute; a plain path string is used as-is."""
    return getattr(file_obj, "name", None) or (file_obj if isinstance(file_obj, str) else fallback)


def _extract_pages(file_obj, doc_name: str) -> list:
    """Returns a list of '--- Document: X, Page N ---\\n<text>' strings for
    every page of one PDF."""
    pages_text = []
    try:
        with pdfplumber.open(file_obj) as pdf:
            if len(pdf.pages) == 0:
                raise PDFExtractionError(f"{doc_name}: the PDF has no pages.")
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                pages_text.append(f"--- Document: {doc_name}, Page {i} ---\n{text}")
    except PDFExtractionError:
        raise
    except Exception as e:
        raise PDFExtractionError(f"Could not read {doc_name}: {e}") from e
    return pages_text


def extract_text_from_pdf(file_obj) -> str:
    """
    Extract text from every page of a SINGLE PDF (used for the sample RFP
    and anywhere only one document is involved). Returns the concatenated
    text of all pages, with "--- Document: X, Page N ---" markers so the AI
    engine can still cite page/document if useful.
    """
    doc_name = _get_display_name(file_obj, "document.pdf")
    pages_text = _extract_pages(file_obj, doc_name)
    full_text = "\n\n".join(pages_text).strip()

    if not full_text or len(full_text.replace("-", "").replace("Page", "").replace("Document", "").strip()) < 20:
        raise PDFExtractionError(
            f"No readable text was found in {doc_name}. It may be a scanned image "
            "rather than a text-based PDF — OCR isn't supported yet, so try a "
            "text-based export of the document instead."
        )
    return full_text


def extract_text_from_documents(file_objs: list) -> tuple:
    """
    Combines MULTIPLE PDF files (e.g. the main RFP + Exhibit A + Exhibit B)
    into ONE piece of text for a single combined analysis. Every page is
    marked with its source document name AND page number, so downstream
    prompts can ask the model to cite exactly which file a deliverable or
    checklist item came from.

    Returns: (combined_text: str, doc_names: list[str])
    Raises PDFExtractionError if ANY file can't be read, naming that file.
    """
    if not file_objs:
        raise PDFExtractionError("No documents were provided.")

    all_pages = []
    doc_names = []
    for f in file_objs:
        doc_name = _get_display_name(f, "document.pdf")
        doc_names.append(doc_name)
        all_pages.extend(_extract_pages(f, doc_name))

    full_text = "\n\n".join(all_pages).strip()

    if not full_text or len(full_text.replace("-", "").replace("Page", "").replace("Document", "").strip()) < 20:
        raise PDFExtractionError(
            "No readable text was found across the uploaded document(s). They may be "
            "scanned images rather than text-based PDFs — OCR isn't supported yet, so "
            "try text-based exports instead."
        )

    return full_text, doc_names
