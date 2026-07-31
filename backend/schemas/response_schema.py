"""
backend/schemas/response_schema.py

Structured-output schemas for the Compliance Agent and Risk Agent.
The Decision Agent does NOT call Gemini at all (per Phase 1 requirements —
"Do not let Gemini randomly decide scoring"), so it has no schema here; it
consumes compliance_results/risks and reuses scoring.py + decision_rules.py.
"""

from typing import List, Literal

from pydantic import BaseModel, Field


class ComplianceResultItem(BaseModel):
    item: str
    department: str
    status: Literal["MET", "GAP", "REVIEW"]
    reason: str


class ComplianceBatch(BaseModel):
    """One Gemini call answers a whole department's worth of checklist
    items at once (same batching strategy ai_engine.py already uses, kept
    to avoid the same 'too many states for serving' schema-size problem)."""
    items: List[ComplianceResultItem] = Field(default_factory=list)


class RiskItem(BaseModel):
    risk: str
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    impact: str
    recommendation: str


class RiskBatch(BaseModel):
    risks: List[RiskItem] = Field(default_factory=list, min_length=1)
