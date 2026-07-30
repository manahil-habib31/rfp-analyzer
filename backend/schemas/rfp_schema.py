"""
backend/schemas/rfp_schema.py

Structured-output schema for the RFP Agent. This agent only EXTRACTS facts
from the RFP text (title, deadline, budget, duration, mandatory
requirements, deliverables, evaluation criteria) — it never scores or
decides anything, so this schema deliberately has no status/verdict/score
fields.

Reuses the existing Deliverable / Criterion models from the root
schemas.py so the shape stays compatible with pdf_report.py, which already
knows how to render those.
"""

from typing import List, Optional

from pydantic import BaseModel, Field

from schemas import Deliverable, Criterion  # existing root-level models


class MandatoryRequirement(BaseModel):
    requirement: str
    docRef: Optional[str] = None
    sectionRef: Optional[str] = None


class RFPExtraction(BaseModel):
    """Everything the RFP Agent is responsible for producing."""
    title: Optional[str] = None
    deadline: Optional[str] = None
    deadlineISO: Optional[str] = None
    budget: Optional[str] = None
    contractDuration: Optional[str] = None
    # Captured specifically so the Decision Agent can reuse the existing
    # deterministic hard-rule thresholds in decision_rules.py (Payment
    # Terms / Insurance Requirements) rather than re-deciding them.
    paymentTermsDays: Optional[int] = None
    insuranceAmountUSD: Optional[float] = None
    mandatoryRequirements: List[MandatoryRequirement] = Field(default_factory=list)
    deliverables: List[Deliverable] = Field(default_factory=list, min_length=1)
    evaluationCriteria: List[Criterion] = Field(default_factory=list)
