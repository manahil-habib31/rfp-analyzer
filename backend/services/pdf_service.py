"""
backend/services/pdf_service.py

Handles PDF upload storage and text extraction for the FastAPI layer.
Reuses pdf_reader.py's extract_text_from_documents() untouched — this
module only owns "where do uploaded files live on disk" (same simple
folder-per-rfp_id pattern the existing api.py already uses).
"""

import glob
import os
from typing import List, Tuple

from pdf_reader import extract_text_from_documents, PDFExtractionError  # noqa: F401 (re-exported)

STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage")
os.makedirs(STORAGE_DIR, exist_ok=True)


class _StoredFileAdapter:
    """Duck-types a file already saved on disk to what pdf_reader.py
    expects (.name + read/seek/tell) — same pattern as api.py's adapter."""

    def __init__(self, path: str):
        self.name = os.path.basename(path)
        self._fh = open(path, "rb")

    def read(self, *args, **kwargs):
        return self._fh.read(*args, **kwargs)

    def seek(self, *args, **kwargs):
        return self._fh.seek(*args, **kwargs)

    def tell(self, *args, **kwargs):
        return self._fh.tell(*args, **kwargs)


def next_rfp_id() -> int:
    existing = [int(d) for d in os.listdir(STORAGE_DIR) if d.isdigit()]
    return (max(existing) + 1) if existing else 1


def save_upload(filename: str, content: bytes) -> int:
    """Saves one uploaded PDF under a fresh rfp_id folder. Returns the id."""
    rfp_id = next_rfp_id()
    rfp_dir = os.path.join(STORAGE_DIR, str(rfp_id))
    os.makedirs(rfp_dir, exist_ok=True)
    with open(os.path.join(rfp_dir, filename), "wb") as f:
        f.write(content)
    return rfp_id


def add_to_existing(rfp_id: int, filename: str, content: bytes) -> None:
    """Adds another exhibit/attachment file to an already-created rfp_id."""
    rfp_dir = os.path.join(STORAGE_DIR, str(rfp_id))
    os.makedirs(rfp_dir, exist_ok=True)
    with open(os.path.join(rfp_dir, filename), "wb") as f:
        f.write(content)


def rfp_dir_exists(rfp_id: int) -> bool:
    return os.path.isdir(os.path.join(STORAGE_DIR, str(rfp_id)))


def list_pdf_paths(rfp_id: int) -> List[str]:
    rfp_dir = os.path.join(STORAGE_DIR, str(rfp_id))
    return sorted(glob.glob(os.path.join(rfp_dir, "*.pdf")))


def extract_text_for_rfp(rfp_id: int) -> Tuple[str, List[str]]:
    """Returns (combined_text, doc_names) for every PDF stored under rfp_id.
    Raises PDFExtractionError (re-exported from pdf_reader.py) on failure."""
    paths = list_pdf_paths(rfp_id)
    if not paths:
        raise PDFExtractionError(f"RFP {rfp_id} has no PDF documents stored.")
    adapted = [_StoredFileAdapter(p) for p in paths]
    return extract_text_from_documents(adapted)
