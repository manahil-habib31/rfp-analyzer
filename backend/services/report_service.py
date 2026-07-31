"""
backend/services/report_service.py

Bridges the new multi-agent `report_data` shape back to the exact dict
shape `pdf_report.generate_pdf_report()` already knows how to render
(category/GO-NO-GO-REVIEW compliance rows, "note"-keyed risks, etc.) — so
the existing PDF generator is reused UNCHANGED, per the brief.
"""

from typing import Any, Dict

from pdf_report import generate_pdf_report  # existing, untouched

_STATUS_TO_LEGACY = {"MET": "GO", "GAP": "NO-GO", "REVIEW": "REVIEW"}


def _adapt_compliance(compliance_results: list) -> list:
    adapted = []
    for c in compliance_results or []:
        adapted.append({
            "category": c.get("department"),
            "item": c.get("item"),
            "status": _STATUS_TO_LEGACY.get(c.get("status"), "REVIEW"),
            "reason": c.get("reason", ""),
            "evidence": c.get("evidence"),
        })
    return adapted


def _adapt_risks(risks: list) -> list:
    adapted = []
    for r in risks or []:
        note = r.get("impact", "")
        if r.get("recommendation"):
            note = f"{note} Recommendation: {r['recommendation']}".strip()
        adapted.append({"risk": r.get("risk", ""), "severity": r.get("severity", "MEDIUM"), "note": note})
    return adapted


def build_report_payload(report_data: Dict[str, Any]) -> Dict[str, Any]:
    """Translates RFPState.report_data (MET/GAP/REVIEW, department-keyed)
    into the legacy analysis dict pdf_report.py expects."""
    verdict = report_data.get("verdict", {}) or {}
    return {
        "rfpIdentifier": report_data.get("title"),
        "verdict": verdict,
        "departmentScores": report_data.get("departmentScores", {}),
        "deliverables": report_data.get("deliverables", []),
        "evaluationCriteria": report_data.get("evaluationCriteria", []),
        "keyDatesBudget": {
            "submissionDeadline": report_data.get("deadline"),
            "contractValueUSD": None,
        },
        "risks": _adapt_risks(report_data.get("risks", [])),
        "compliance": _adapt_compliance(report_data.get("compliance", [])),
    }


def generate_report_bytes(report_data: Dict[str, Any], source_label: str) -> bytes:
    """Full pipeline: adapt -> generate_pdf_report() (existing, unmodified)."""
    payload = build_report_payload(report_data)
    return generate_pdf_report(payload, source_label)
