"""
backend/graph/workflow.py

Builds and runs the LangGraph workflow that replaces the old single Gemini
call in ai_engine.analyze_rfp() with four controlled, sequential agent
steps:

    START -> RFP Agent -> Compliance Agent -> Risk Agent -> Decision Agent -> END

Each node is a plain function (state) -> partial_state_update, exactly the
shape langgraph.graph.StateGraph expects. The graph is compiled once at
import time and reused across requests.
"""

from typing import Any, Dict, List, Optional

from langgraph.graph import StateGraph, START, END

from backend.agents import rfp_agent, compliance_agent, risk_agent, decision_agent
from backend.graph.state import RFPState, new_state


def _build_graph():
    graph = StateGraph(RFPState)

    graph.add_node("rfp_agent", rfp_agent.run)
    graph.add_node("compliance_agent", compliance_agent.run)
    graph.add_node("risk_agent", risk_agent.run)
    graph.add_node("decision_agent", decision_agent.run)

    graph.add_edge(START, "rfp_agent")
    graph.add_edge("rfp_agent", "compliance_agent")
    graph.add_edge("compliance_agent", "risk_agent")
    graph.add_edge("risk_agent", "decision_agent")
    graph.add_edge("decision_agent", END)

    return graph.compile()


_compiled_graph = _build_graph()


def run_workflow(
    rfp_text: str,
    company_profile: Dict[str, Any],
    api_key: str,
    checklist: Optional[List[Dict[str, Any]]] = None,
    doc_names: Optional[List[str]] = None,
    rfp_id: Optional[int] = None,
) -> RFPState:
    """
    Executes the full graph and returns the COMPLETE final RFPState
    (all four agents' outputs merged onto the initial state).
    """
    from checklist_items import CHECKLIST_ITEMS  # existing 34-item checklist

    state = new_state(
        rfp_text=rfp_text,
        company_profile=company_profile,
        checklist=checklist or CHECKLIST_ITEMS,
        doc_names=doc_names,
        rfp_id=rfp_id,
    )
    # Passed through state (not a module-level global) so concurrent runs
    # with different keys never collide; agents pop it via state["_api_key"].
    state["_api_key"] = api_key  # type: ignore[typeddict-item]

    final_state = _compiled_graph.invoke(state)
    final_state.pop("_api_key", None)
    return final_state
