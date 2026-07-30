"""
backend/agents/rfp_agent.py

FIRST node in the LangGraph workflow.

Responsibility: read `state.rfp_text` and extract, as structured JSON:
  - title, deadline, budget, contract duration
  - mandatory requirements
  - deliverables
  - evaluation criteria

This agent makes NO decisions (no GO/NO-GO, no scoring, no compliance
judgment) — that is intentionally left to the Compliance/Risk/Decision
agents downstream, matching the brief.
"""

from typing import Any, Dict

from backend.graph.state import RFPState
from backend.llm.gemini_client import get_client, QuotaExhaustedError, AnalysisError
from backend.schemas.rfp_schema import RFPExtraction


def _build_prompt(company_profile: Dict[str, Any], doc_names) -> str:
    profile_lines = "\n".join(f"- {k}: {v}" for k, v in (company_profile or {}).items())
    doc_note = ""
    if doc_names and len(doc_names) > 1:
        doc_list = "\n".join(f"  - {d}" for d in doc_names)
        doc_note = f"""
This RFP was assembled from {len(doc_names)} separate source documents:
{doc_list}
The text below is prefixed with "--- Document: <filename>, Page N ---" headers.
Use these to fill in docRef/sectionRef on deliverable points and mandatory
requirements wherever possible.
"""
    return f"""You are an RFP extraction assistant. Your ONLY job is to extract facts that
are explicitly present in the RFP text below. Do NOT judge, score, or decide anything —
downstream agents handle compliance, risk, and go/no-go decisions.
{doc_note}
COMPANY PROFILE (for context only — do not use it to score anything here):
{profile_lines}

Extract:
- "title": the RFP's official solicitation/bid number if stated (e.g. "RFP No. 26-CMS-114"),
  otherwise a short 4-8 word descriptive title. Null only if truly unidentifiable.
- "deadline": the submission deadline as written in the text.
- "deadlineISO": the same deadline in YYYY-MM-DD format, or null if it can't be determined.
- "budget": the stated contract value/budget as written (e.g. "$1,200,000"), or null.
- "contractDuration": the stated contract/implementation period (e.g. "12 months"), or null.
- "paymentTermsDays": the payment terms in days as an integer (e.g. "NET30" -> 30), or null.
- "insuranceAmountUSD": the required insurance coverage amount in USD as a number, or null.
- "mandatoryRequirements": every requirement the RFP explicitly marks as mandatory/required
  (eligibility, certifications, minimum experience, bonding, insurance minimums, etc.). Each
  item has "requirement", "docRef" (source filename if multi-document, else null), and
  "sectionRef" (section/clause number if named, else null).
- "deliverables": every distinct document/form/artifact the vendor must submit. Each has
  "description", "mandatory" (true/false), "estimatedDays" (best-effort int or null),
  "priority" ("High"/"Medium"/"Low"), and "points" (2-6 child requirement/description items,
  each with "point", "docRef", "sectionRef", "pageRef" — all null when not determinable).
- "evaluationCriteria": [{{"criterion", "weightPercent"}}], ordered by weight descending.

Respond with ONLY a raw JSON object (no commentary, no markdown fences)."""


def run(state: RFPState) -> Dict[str, Any]:
    """LangGraph node function: takes the current state, returns the partial
    update to merge in (`extracted_requirements`, plus any warnings)."""
    company_profile = state.get("company_profile", {})
    rfp_text = state.get("rfp_text", "")
    doc_names = state.get("doc_names", [])
    errors = list(state.get("errors", []))

    client = get_client(state["_api_key"])  # injected by the workflow runner
    prompt = _build_prompt(company_profile, doc_names)

    try:
        result = client.generate(
            system_prompt=prompt,
            content="RFP TEXT:\n\n" + rfp_text,
            response_schema=RFPExtraction,
            max_output_tokens=16384,
        )
        extracted = result.model_dump() if hasattr(result, "model_dump") else _fallback_parse(result)
    except (QuotaExhaustedError, AnalysisError) as e:
        errors.append(f"RFP Agent: {e}")
        extracted = {
            "title": None, "deadline": None, "deadlineISO": None, "budget": None,
            "contractDuration": None, "mandatoryRequirements": [], "deliverables": [],
            "evaluationCriteria": [],
        }

    return {"extracted_requirements": extracted, "errors": errors}


def _fallback_parse(raw_text: str) -> dict:
    import json
    cleaned = raw_text.strip().strip("`")
    if cleaned.lower().startswith("json"):
        cleaned = cleaned[4:]
    return json.loads(cleaned.strip())
