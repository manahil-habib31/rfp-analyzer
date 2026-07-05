"""
pdf_reader.py

Extracts plain text from an uploaded RFP PDF using pdfplumber. Kept as its own
module so the extraction strategy (and any future OCR fallback for scanned
PDFs) can change without touching the UI or the AI engine.
"""

import pdfplumber


class PDFExtractionError(Exception):
    """Raised when a PDF can't be read or contains no extractable text."""
    pass


def extract_text_from_pdf(file_obj) -> str:
    """
    Extract text from every page of a PDF.

    `file_obj` can be a file path (str) or a file-like object (e.g. Streamlit's
    UploadedFile), since pdfplumber accepts both.

    Returns the concatenated text of all pages, with page breaks marked so the
    AI engine can still reference "page N" if useful.
    """
    pages_text = []
    try:
        with pdfplumber.open(file_obj) as pdf:
            if len(pdf.pages) == 0:
                raise PDFExtractionError("The PDF has no pages.")
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                pages_text.append(f"--- Page {i} ---\n{text}")
    except PDFExtractionError:
        raise
    except Exception as e:
        raise PDFExtractionError(f"Could not read this PDF: {e}") from e

    full_text = "\n\n".join(pages_text).strip()

    if not full_text or len(full_text.replace("-", "").replace("Page", "").strip()) < 20:
        raise PDFExtractionError(
            "No readable text was found in this PDF. It may be a scanned image "
            "rather than a text-based PDF — OCR isn't supported yet, so try a "
            "text-based export of the document instead."
        )

    return full_text
