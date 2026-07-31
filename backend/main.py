"""
backend/main.py

FastAPI layer for the Phase 1 multi-agent architecture.

Endpoints:
    POST /api/upload            -> store PDF(s), returns a numeric rfp_id
    POST /api/analyze/{rfp_id}  -> runs the LangGraph workflow (RFP -> Compliance ->
                                    Risk -> Decision agents), caches + returns the state
    GET  /api/result/{rfp_id}   -> returns the cached JSON result
    POST /api/report/{rfp_id}   -> generates the PDF report via the existing pdf_report.py

RUN LOCALLY (from the repo root):
    pip install -r requirements.txt
    pip install langgraph langchain
    python -m uvicorn backend.main:app --reload --port 8000

Then open http://127.0.0.1:8000/docs for the Swagger UI.

This file does NOT remove or modify app.py (Streamlit) or api.py (the
existing single-call FastAPI wrapper) — both keep working exactly as
before. This is an ADDITIONAL entrypoint, run on a different port if you
want both up at once (`--port 8000` here vs `--port 8001` for api.py, e.g.).
"""

import os
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from checklist_items import DEFAULT_COMPANY_PROFILE, CHECKLIST_ITEMS

from backend.graph.workflow import run_workflow
from backend.services import pdf_service, report_service
from backend.services.pdf_service import PDFExtractionError

load_dotenv()

app = FastAPI(
    title="RFP Analyzer — Multi-Agent API (Phase 1)",
    description=(
        "LangGraph-controlled RFP analysis: RFP Agent -> Compliance Agent -> "
        "Risk Agent -> Decision Agent. Preserves the existing 34-item SPS "
        "checklist, scoring rules, and PDF report generator."
    ),
    version="1.0.0-phase1",
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# In-memory cache of completed workflow runs, keyed by rfp_id.
_results_cache: dict = {}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/upload")
async def upload(
    file1: UploadFile = File(..., description="The main RFP document (required)."),
    file2: Optional[UploadFile] = File(None, description="Optional Exhibit/Attachment #2."),
    file3: Optional[UploadFile] = File(None, description="Optional Exhibit/Attachment #3."),
):
    """Saves the uploaded PDF(s) to disk and returns a new rfp_id. Nothing
    is analyzed yet — call /api/analyze/{rfp_id} separately."""
    uploaded: List[UploadFile] = [f for f in [file1, file2, file3] if f is not None]

    first = uploaded[0]
    rfp_id = pdf_service.save_upload(first.filename, await first.read())
    for f in uploaded[1:]:
        pdf_service.add_to_existing(rfp_id, f.filename, await f.read())

    return JSONResponse(content={"rfp_id": rfp_id, "documents": [f.filename for f in uploaded]})


@app.post("/api/analyze/{rfp_id}")
def analyze(rfp_id: int, force: bool = False):
    """Executes the LangGraph workflow (all four agents, in order) for a
    previously uploaded RFP and returns the complete final state."""
    if not force and rfp_id in _results_cache:
        return JSONResponse(content=_results_cache[rfp_id])

    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: GEMINI_API_KEY is not set.")

    if not pdf_service.rfp_dir_exists(rfp_id):
        raise HTTPException(status_code=404, detail=f"No RFP found with id {rfp_id}. Upload it first via /api/upload.")

    try:
        rfp_text, doc_names = pdf_service.extract_text_for_rfp(rfp_id)
    except PDFExtractionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    final_state = run_workflow(
        rfp_text=rfp_text,
        company_profile=DEFAULT_COMPANY_PROFILE,
        api_key=GEMINI_API_KEY,
        checklist=CHECKLIST_ITEMS,
        doc_names=doc_names,
        rfp_id=rfp_id,
    )

    _results_cache[rfp_id] = final_state
    return JSONResponse(content=final_state)


@app.get("/api/result/{rfp_id}")
def result(rfp_id: int):
    """Returns the cached JSON result of a previous /api/analyze call."""
    if rfp_id not in _results_cache:
        raise HTTPException(
            status_code=404,
            detail=f"No result cached for RFP {rfp_id} yet. Call POST /api/analyze/{rfp_id} first.",
        )
    return JSONResponse(content=_results_cache[rfp_id])


@app.post("/api/report/{rfp_id}")
def report(rfp_id: int):
    """Generates the PDF report for a previously analyzed RFP, using the
    existing pdf_report.py generator unchanged."""
    if rfp_id not in _results_cache:
        raise HTTPException(
            status_code=404,
            detail=f"No result cached for RFP {rfp_id} yet. Call POST /api/analyze/{rfp_id} first.",
        )
    report_data = _results_cache[rfp_id].get("report_data", {})
    pdf_bytes = report_service.generate_report_bytes(report_data, source_label=f"RFP #{rfp_id}")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="rfp_{rfp_id}_report.pdf"'},
    )
