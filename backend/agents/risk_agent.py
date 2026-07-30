"""
backend/agents/risk_agent.py

THIRD node in the LangGraph workflow.

Responsibility: analyze missing requirements, eligibility problems,
financial risks, timeline risks, and technical risks — using the RFP
Agent's extraction AND the Compliance Agent's GAP/REVIEW items as input
(so risk analysis is grounded in what's already been found, not a fresh
re-read of the raw PDF). Returns:

    {"risk": "", "severity": "", "impact": "", "recommendation": ""}
"""

from typing import Any, Dict, List

from backend.graph.state import RFPState
from backend.llm.gemini_client import get_client, QuotaExhaustedError, AnalysisError
from backend.schemas.response_schema import RiskBatch


def _build_prompt(company_profile: Dict[str, Any], extracted: Dict[str, Any], compliance_results: List[dict]) -> str:
    profile_lines = "\n".join(f"- {k}: {v}" for k, v in (company_profile or {}).items())

    gaps = [c for c in compliance_results if c.get("status") in ("GAP", "REVIEW")]
    gap_lines = "\n".join(f"- [{g['status']}] {g['item']} ({g['department']}): {g.get('reason','')}" for g in gaps) \
        or "- None flagged."

    mandatory = "; ".join(r.get("requirement", "") for r in extracted.get("mandatoryRequirements", [])) or "None extracted."

    return f"""You are an RFP risk assessment assistant. Analyze the inputs below and identify the
most significant risks to pursuing this bid, covering (where applicable):
- missing or unclear requirements
- eligibility problems
- financial risks (payment terms, insurance, bonding, pricing)
- timeline risks (deadline pressure, contract duration)
- technical risks (capability gaps, ambiguous scope)

COMPANY PROFILE:
{profile_lines}

RFP KEY FACTS:
- Title: {extracted.get('title')}
- Deadline: {extracted.get('deadline')}
- Budget: {extracted.get('budget')}
- Contract duration: {extracted.get('contractDuration')}
- Mandatory requirements: {mandatory}

COMPLIANCE FINDINGS FLAGGED AS GAP OR REVIEW (highest-signal risk source):
{gap_lines}

Return 3-8 risk entries. For EACH:
- "risk": short description of the risk itself.
- "severity": "HIGH", "MEDIUM", or "LOW".
- "impact": what happens if this risk materializes (1-2 sentences).
- "recommendation": a concrete mitigation or next step.

Respond with ONLY a raw JSON object (no commentary, no markdown fences)."""


def run(state: RFPState) -> Dict[str, Any]:
    company_profile = state.get("company_profile", {})
    extracted = state.get("extracted_requirements", {})
    compliance_results = state.get("compliance_results", [])
    errors = list(state.get("errors", []))

    client = get_client(state["_api_key"])
    prompt = _build_prompt(company_profile, extracted, compliance_results)

    try:
        result = client.generate(
            system_prompt=prompt,
            content="Analyze the RFP based on the facts and compliance findings provided above.",
            response_schema=RiskBatch,
            max_output_tokens=4096,
        )
        risks = (result.model_dump()["risks"] if hasattr(result, "model_dump") else _fallback_parse(result))
    except (QuotaExhaustedError, AnalysisError) as e:
        errors.append(f"Risk Agent: {e}")
        risks = []

    return {"risks": risks, "errors": errors}


def _fallback_parse(raw_text: str) -> list:
    import json
    cleaned = raw_text.strip().strip("`")
    if cleaned.lower().startswith("json"):
        cleaned = cleaned[4:]
    parsed = json.loads(cleaned.strip())
    return parsed.get("risks", parsed if isinstance(parsed, list) else [])
