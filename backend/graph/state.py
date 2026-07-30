"""
backend/graph/state.py

The single shared state object that flows through every node of the
LangGraph workflow (RFP Agent -> Compliance Agent -> Risk Agent ->
Decision Agent). Each agent reads what it needs and writes its own
section back onto the same state — nothing is thrown away between nodes,
so the final state returned by the graph is a complete record of the run.

Implemented as a TypedDict (not a Pydantic BaseModel) because that's what
langgraph.graph.StateGraph expects natively: each node returns a partial
dict of the keys it updates, and LangGraph merges that into the running
state for you.
"""

from typing import Any, Dict, List, Optional, TypedDict


class RFPState(TypedDict, total=False):
    # ---- inputs ----
    rfp_id: Optional[int]
    rfp_text: str
    doc_names: List[str]
    company_profile: Dict[str, Any]
    checklist: List[Dict[str, Any]]          # fixed 34-item SPS checklist (from checklist_items.py)

    # ---- RFP Agent output ----
    extracted_requirements: Dict[str, Any]   # title, deadline, budget, duration, mandatory reqs,
                                              # deliverables, evaluation criteria

    # ---- Compliance Agent output ----
    compliance_results: List[Dict[str, Any]]  # [{item, department, status, reason}, ...]
    department_scores: Dict[str, Any]         # deterministic scores from scoring.py

    # ---- Risk Agent output ----
    risks: List[Dict[str, Any]]              # [{risk, severity, impact, recommendation}, ...]

    # ---- Decision Agent output ----
    final_score: Dict[str, Any]              # {score, breakdown}
    verdict: str                             # "GO" | "CONDITIONAL" | "NO-GO"

    # ---- assembled for the existing PDF report generator ----
    report_data: Dict[str, Any]

    # ---- bookkeeping ----
    errors: List[str]                        # non-fatal warnings collected along the way


def new_state(
    rfp_text: str,
    company_profile: Dict[str, Any],
    checklist: List[Dict[str, Any]],
    doc_names: Optional[List[str]] = None,
    rfp_id: Optional[int] = None,
) -> RFPState:
    """Builds the initial state handed to the graph's entry node."""
    return RFPState(
        rfp_id=rfp_id,
        rfp_text=rfp_text,
        doc_names=doc_names or [],
        company_profile=company_profile,
        checklist=checklist,
        extracted_requirements={},
        compliance_results=[],
        department_scores={},
        risks=[],
        final_score={},
        verdict="",
        report_data={},
        errors=[],
    )
