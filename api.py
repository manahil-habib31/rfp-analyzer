"""
api.py

A minimal FastAPI wrapper around the existing RFP Analyzer engine
(ai_engine.py, pdf_reader.py, checklist_items.py) so the analysis can be
triggered and consumed as PURE JSON — no Streamlit frontend required.

TWO-STEP FLOW (so a live demo/evaluation never needs a file picker on the
spot — only a number):

    1. POST /upload            -> upload the RFP's PDF(s) ONCE, ahead of time.
                                   Returns a numeric "rfp_id".
    2. GET  /analyze/{rfp_id}  -> given JUST that number, runs (or re-serves
                                   a cached) analysis and returns JSON.

    Bonus: GET /rfps lists every uploaded RFP and its id, so you always know
    which number to use without having to remember it.

Drop this file into the SAME folder as app.py, ai_engine.py, pdf_reader.py,
schemas.py, checklist_items.py, decision_rules.py, and scoring.py — it
reuses all of that logic directly rather than duplicating it.

RUN LOCALLY:
    pip install fastapi uvicorn python-multipart python-dotenv
    python -m uvicorn api:app --reload --port 8000

Then open http://127.0.0.1:8000/docs for the built-in Swagger UI.

AUTHENTICATION:
Every request must include a header:
    X-API-Key: <value of RFP_API_KEY from your .env / environment>

This is a SEPARATE key from GEMINI_API_KEY — RFP_API_KEY protects OUR
endpoints from being called by strangers; GEMINI_API_KEY is what our server
uses internally to call Google's Gemini API. Add both to your .env file:
    GEMINI_API_KEY=your-gemini-key-here
    RFP_API_KEY=choose-any-secret-string-here

STORAGE: uploaded PDFs are saved to a local "storage/<rfp_id>/" folder next
to this file. This is intentionally simple (no database) — good enough for
a course project / demo. It resets if that folder is deleted.
"""

import os
import glob
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from pdf_reader import extract_text_from_documents, PDFExtractionError
from ai_engine import analyze_rfp, QuotaExhaustedError, AnalysisError
from checklist_items import DEFAULT_COMPANY_PROFILE

load_dotenv()

app = FastAPI(
    title="RFP Analyzer API",
    description=(
        "Two-step flow: upload an RFP's PDF(s) once via /upload to get a numeric "
        "rfp_id, then fetch its JSON analysis anytime via /analyze/{rfp_id} using "
        "just that number — no file picker needed at analysis time."
    ),
    version="2.0.0",
)

API_KEY = os.environ.get("RFP_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

STORAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage")
os.makedirs(STORAGE_DIR, exist_ok=True)

# In-memory cache of completed analyses, keyed by rfp_id — so hitting
# /analyze/{id} again (e.g. an evaluator clicking Execute twice) doesn't
# burn extra Gemini calls or make them wait through the ~30-60s analysis
# again. Cleared automatically whenever the server restarts.
_results_cache: dict = {}


class _StoredFileAdapter:
    """Adapts a file already saved on disk to the same duck-typed shape
    pdf_reader.py expects (.name + read/seek/tell) — the same pattern used
    for FastAPI's UploadFile, just backed by a real opened file instead of
    an in-memory upload."""
    def __init__(self, path: str):
        self.name = os.path.basename(path)
        self._fh = open(path, "rb")

    def read(self, *args, **kwargs):
        return self._fh.read(*args, **kwargs)

    def seek(self, *args, **kwargs):
        return self._fh.seek(*args, **kwargs)

    def tell(self, *args, **kwargs):
        return self._fh.tell(*args, **kwargs)


def _verify_api_key(x_api_key: Optional[str]):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: RFP_API_KEY is not set.")
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key. Send it as header: X-API-Key")


def _next_rfp_id() -> int:
    existing = [int(d) for d in os.listdir(STORAGE_DIR) if d.isdigit()]
    return (max(existing) + 1) if existing else 1


@app.get("/health")
def health():
    """Simple liveness check — no API key required."""
    return {"status": "ok"}


@app.post("/upload")
async def upload(
    file1: UploadFile = File(..., description="The main RFP document (required)."),
    file2: Optional[UploadFile] = File(None, description="Optional Exhibit/Attachment #2."),
    file3: Optional[UploadFile] = File(None, description="Optional Exhibit/Attachment #3."),
    file4: Optional[UploadFile] = File(None, description="Optional Exhibit/Attachment #4."),
    file5: Optional[UploadFile] = File(None, description="Optional Exhibit/Attachment #5."),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    Saves the uploaded PDF(s) to disk under a new numeric rfp_id and returns
    that id. Nothing is analyzed yet — call /analyze/{rfp_id} separately
    whenever you're ready (that's the step that costs Gemini API calls).
    """
    _verify_api_key(x_api_key)

    uploaded = [f for f in [file1, file2, file3, file4, file5] if f is not None]
    rfp_id = _next_rfp_id()
    rfp_dir = os.path.join(STORAGE_DIR, str(rfp_id))
    os.makedirs(rfp_dir, exist_ok=True)

    saved_names = []
    for f in uploaded:
        dest_path = os.path.join(rfp_dir, f.filename)
        with open(dest_path, "wb") as out:
            out.write(await f.read())
        saved_names.append(f.filename)

    return JSONResponse(content={"rfp_id": rfp_id, "documents": saved_names})


@app.get("/rfps")
def list_rfps(x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    """Lists every uploaded RFP's id and its document filenames — so you
    always know which number to pass to /analyze/{rfp_id}."""
    _verify_api_key(x_api_key)
    listing = []
    for entry in sorted(os.listdir(STORAGE_DIR), key=lambda x: int(x) if x.isdigit() else -1):
        if not entry.isdigit():
            continue
        rfp_dir = os.path.join(STORAGE_DIR, entry)
        docs = sorted(os.path.basename(p) for p in glob.glob(os.path.join(rfp_dir, "*.pdf")))
        listing.append({
            "rfp_id": int(entry),
            "documents": docs,
            "analyzed": int(entry) in _results_cache,
        })
    return JSONResponse(content={"rfps": listing})


@app.get("/analyze/{rfp_id}")
def analyze_by_id(
    rfp_id: int,
    force: bool = Query(False, description="Set true to re-run analysis even if a cached result exists."),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    Runs (or re-serves a cached) analysis for a previously uploaded RFP,
    given ONLY its numeric id — no file needed at this step. This is the
    endpoint to use for a live demo/evaluation: upload once beforehand via
    /upload, then just call this with the resulting number.
    """
    _verify_api_key(x_api_key)

    if not force and rfp_id in _results_cache:
        return JSONResponse(content=_results_cache[rfp_id])

    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: GEMINI_API_KEY is not set.")

    rfp_dir = os.path.join(STORAGE_DIR, str(rfp_id))
    if not os.path.isdir(rfp_dir):
        raise HTTPException(status_code=404, detail=f"No RFP found with id {rfp_id}. Upload it first via /upload.")

    file_paths = sorted(glob.glob(os.path.join(rfp_dir, "*.pdf")))
    if not file_paths:
        raise HTTPException(status_code=404, detail=f"RFP {rfp_id} has no PDF documents stored.")

    adapted_files = [_StoredFileAdapter(p) for p in file_paths]

    try:
        rfp_text, doc_names = extract_text_from_documents(adapted_files)
    except PDFExtractionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        result = analyze_rfp(rfp_text, DEFAULT_COMPANY_PROFILE, GEMINI_API_KEY, doc_names=doc_names)
    except QuotaExhaustedError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except AnalysisError as e:
        raise HTTPException(status_code=502, detail=str(e))

    _results_cache[rfp_id] = result
    return JSONResponse(content=result)
