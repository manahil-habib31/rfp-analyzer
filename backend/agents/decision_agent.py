"""
backend/agents/decision_agent.py

FOURTH and final node in the LangGraph workflow.

Responsibility: compute the final score and GO / CONDITIONAL / NO-GO
verdict. This agent NEVER calls Gemini — scoring stays fully deterministic
and reproducible, reusing the existing scoring.py (department/overall
compliance scoring) and decision_rules.py (hard Payment Terms / Insurance
threshold rules) exactly as the current single-call pipeline does.

Sub-scores that used to come from the AI's free-form judgment
(strategicFit, financialTermsFit, riskLevel) are replaced here with
deterministic, rule-based equivalents derived from the Compliance and Risk
agents' own structured output, so nothing about the final number is a
model guess.
"""

from typing import Any, Dict

from decision_rules import apply_hard_rules
from scoring import compute_final_verdict

from backend.graph.state import RFPState

_SEVERITY_PENALTY = {"HIGH": 25, "MEDIUM": 12, "LOW": 5}


def _deterministic_risk_score(risks: list) -> int:
    """100 = no risk. Each HIGH/MEDIUM/LOW risk deducts a fixed amount,
    floored at 0 — simple, reproducible, no AI judgment involved."""
    score = 100
    for r in risks or []:
        score -= _SEVERITY_PENALTY.get(r.get("severity"), 10)
    return max(0, score)


def _deterministic_strategic_fit(compliance_results: list) -> int:
    """Reuses the same GO/REVIEW/NO-GO point values as scoring.py, scoped
    to the 'legal' + 'technical' style items which most reflect scope fit."""
    if not compliance_results:
        return 50
    points = {"MET": 100, "REVIEW": 50, "GAP": 0}
    scores = [points.get(c.get("status"), 50) for c in compliance_results]
    return round(sum(scores) / len(scores))


def _deterministic_financial_fit(compliance_results: list) -> int:
    financial_items = [c for c in compliance_results if c.get("department") == "financial"]
    if not financial_items:
        return 50
    points = {"MET": 100, "REVIEW": 50, "GAP": 0}
    scores = [points.get(c.get("status"), 50) for c in financial_items]
    return round(sum(scores) / len(scores))


def run(state: RFPState) -> Dict[str, Any]:
    extracted = state.get("extracted_requirements", {})
    company_profile = state.get("company_profile", {})
    compliance_results = state.get("compliance_results", [])
    department_scores = state.get("department_scores", {})
    risks = state.get("risks", [])

    # decision_rules.apply_hard_rules() expects the LEGACY GO/NO-GO/REVIEW
    # vocabulary and a "compliance" list keyed the same way ai_engine.py
    # already produces it — translate just for this call, without mutating
    # the MET/GAP/REVIEW results the API/report will actually show.
    status_map = {"MET": "GO", "GAP": "NO-GO", "REVIEW": "REVIEW"}
    legacy_compliance = [
        {**c, "status": status_map.get(c["status"], "REVIEW")} for c in compliance_results
    ]

    verdict_components = {
        "strategicFit": {"score": _deterministic_strategic_fit(compliance_results), "note": "Derived from compliance results."},
        "financialTermsFit": {"score": _deterministic_financial_fit(compliance_results), "note": "Derived from financial checklist items."},
        "riskLevel": {"score": _deterministic_risk_score(risks), "note": "Derived from Risk Agent severities."},
        "summary": (
            f"Deterministic assessment for '{extracted.get('title') or 'this RFP'}' based on "
            f"{len(compliance_results)} checklist items and {len(risks)} identified risks."
        ),
    }

    compliance_overall = department_scores.get("overall", {}).get("score", 50)
    blended_verdict = compute_final_verdict(verdict_components, compliance_overall)

    data_for_hard_rules = {
        "verdict": blended_verdict,
        "compliance": legacy_compliance,
        "keyDatesBudget": {
            "paymentTermsDays": extracted.get("paymentTermsDays"),
            "insuranceAmountUSD": extracted.get("insuranceAmountUSD"),
        },
    }
    result = apply_hard_rules(data_for_hard_rules, company_profile)
    final_verdict_dict = result["verdict"]

    # Translate the (possibly hard-rule-adjusted) compliance list back to
    # MET/GAP/REVIEW for anything downstream that reads compliance_results.
    reverse_map = {"GO": "MET", "NO-GO": "GAP", "REVIEW": "REVIEW"}
    final_compliance = [
        {**c, "status": reverse_map.get(c["status"], "REVIEW")} for c in result["compliance"]
    ]

    final_score = {
        "score": final_verdict_dict["score"],
        "breakdown": final_verdict_dict["breakdown"],
        "departmentScores": department_scores,
    }

    report_data = {
        "title": extracted.get("title"),
        "deadline": extracted.get("deadline"),
        "budget": extracted.get("budget"),
        "contractDuration": extracted.get("contractDuration"),
        "deliverables": extracted.get("deliverables", []),
        "evaluationCriteria": extracted.get("evaluationCriteria", []),
        "compliance": final_compliance,
        "departmentScores": department_scores,
        "risks": risks,
        "verdict": final_verdict_dict,
    }

    return {
        "compliance_results": final_compliance,
        "final_score": final_score,
        "verdict": final_verdict_dict["tag"],
        "report_data": report_data,
    }
