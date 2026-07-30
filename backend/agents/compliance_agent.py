"""
backend/agents/compliance_agent.py

SECOND node in the LangGraph workflow.

Responsibility: compare (extracted RFP requirements + company profile)
against the existing, fixed 34-item SPS checklist from checklist_items.py
and return, per item:

    {"item": "", "department": "", "status": "MET/GAP/REVIEW", "reason": ""}

Reuses checklist_items.py directly (same categories, same items — the
checklist itself is NOT reinvented here) and scoring.py for the
deterministic department/overall score, exactly as the existing single-call
pipeline does.
"""

from typing import Any, Dict, List

from checklist_items import CHECKLIST_ITEMS, CATEGORY_ORDER, CATEGORY_META
from scoring import compute_scores

from backend.graph.state import RFPState
from backend.llm.gemini_client import get_client, QuotaExhaustedError, AnalysisError
from backend.schemas.response_schema import ComplianceBatch

# The rest of the codebase (scoring.py, decision_rules.py, pdf_report.py)
# speaks GO/NO-GO/REVIEW. The brief for this agent asks for MET/GAP/REVIEW.
# We keep MET/GAP/REVIEW as the agent's own vocabulary (returned to the
# caller as-is) and translate only when feeding the existing scorer.
_STATUS_TO_LEGACY = {"MET": "GO", "GAP": "NO-GO", "REVIEW": "REVIEW"}


def _build_prompt(company_profile: Dict[str, Any], extracted: Dict[str, Any], category: str, doc_names) -> str:
    cat_items = [it for it in CHECKLIST_ITEMS if it["category"] == category]
    item_list = "\n".join(f"{i+1}. {it['item']} — {it['question']}" for i, it in enumerate(cat_items))
    profile_lines = "\n".join(f"- {k}: {v}" for k, v in (company_profile or {}).items())
    cat_title = CATEGORY_META[category]["title"]

    extracted_summary = (
        f"Title: {extracted.get('title')}\n"
        f"Deadline: {extracted.get('deadline')}\n"
        f"Budget: {extracted.get('budget')}\n"
        f"Contract duration: {extracted.get('contractDuration')}\n"
        f"Mandatory requirements: "
        + "; ".join(r.get("requirement", "") for r in extracted.get("mandatoryRequirements", []))
    )

    return f"""You are an RFP compliance assistant. Your ONLY job is to answer a fixed checklist
of {len(cat_items)} {cat_title} items — nothing else.

COMPANY PROFILE:
{profile_lines}

EXTRACTED RFP FACTS (from the RFP Agent):
{extracted_summary}

CHECKLIST — answer every one of these {len(cat_items)} items, in this exact order, using the
exact item name given:
{item_list}

For EACH item, decide:
- "department": "{category}"
- "status": "MET" (requirement satisfied / favorable given the company profile), "GAP"
  (requirement not satisfied, or a hard threshold exceeded), or "REVIEW" (needs human
  judgment, or the RFP doesn't say enough).
- Hard rule for "Payment Terms": NET30 or better -> MET. Worse -> GAP.
- Hard rule for "Insurance Requirements": required coverage <= the company's
  max_insurance_available_usd -> MET. Above it -> GAP.
- "reason": one or two sentences grounded in the RFP text/extracted facts. If the RFP doesn't
  mention the item, say so plainly rather than leaving it out.

It is critical your response contains all {len(cat_items)} items — fewer is invalid."""


def run(state: RFPState) -> Dict[str, Any]:
    company_profile = state.get("company_profile", {})
    extracted = state.get("extracted_requirements", {})
    rfp_text = state.get("rfp_text", "")
    doc_names = state.get("doc_names", [])
    errors = list(state.get("errors", []))

    client = get_client(state["_api_key"])

    all_items: List[dict] = []
    for category in CATEGORY_ORDER:
        cat_count = len([it for it in CHECKLIST_ITEMS if it["category"] == category])
        if cat_count == 0:
            continue
        try:
            prompt = _build_prompt(company_profile, extracted, category, doc_names)
            result = client.generate(
                system_prompt=prompt,
                content="RFP TEXT:\n\n" + rfp_text,
                response_schema=ComplianceBatch,
                max_output_tokens=8192,
            )
            batch = result.model_dump()["items"] if hasattr(result, "model_dump") else _fallback_parse(result)
            all_items.extend(batch)
        except (QuotaExhaustedError, AnalysisError) as e:
            errors.append(f"Compliance Agent ({CATEGORY_META[category]['title']}): {e}")

    merged = _merge_with_checklist(all_items)
    legacy_shaped = [
        {**it, "status": _STATUS_TO_LEGACY.get(it["status"], "REVIEW")} for it in merged
    ]
    department_scores = compute_scores(legacy_shaped)

    return {
        "compliance_results": merged,
        "department_scores": department_scores,
        "errors": errors,
    }


def _merge_with_checklist(ai_items: List[dict]) -> List[dict]:
    """Merges Gemini's answers back onto the FIXED checklist (same pattern as
    ai_engine._merge_compliance) so the result always covers exactly the
    right 34 items regardless of what the model returned or omitted."""
    by_name = {}
    for it in ai_items or []:
        name = (it or {}).get("item")
        if name:
            by_name[name.strip().lower()] = it

    merged = []
    for ci in CHECKLIST_ITEMS:
        found = by_name.get(ci["item"].strip().lower())
        merged.append({
            "item": ci["item"],
            "department": ci["category"],
            "question": ci["question"],
            "status": (found or {}).get("status", "REVIEW"),
            "reason": (found or {}).get(
                "reason", "Not returned by the model — re-run the analysis or check this item manually."
            ),
        })
    return merged


def _fallback_parse(raw_text: str) -> list:
    import json
    cleaned = raw_text.strip().strip("`")
    if cleaned.lower().startswith("json"):
        cleaned = cleaned[4:]
    parsed = json.loads(cleaned.strip())
    return parsed.get("items", parsed if isinstance(parsed, list) else [])
