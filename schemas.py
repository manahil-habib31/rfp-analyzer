"""
schemas.py

Strict Pydantic schema for the analysis response. Passing this to Gemini as
`response_schema` (alongside response_mime_type="application/json") makes the
API *structurally* constrain its output to this shape — this is stronger than
just describing the desired JSON in the prompt text, which is what caused the
compliance checklist to come back empty/misshapen in earlier testing: the
model answered everything else correctly but didn't reliably match the
free-text-described shape for the largest, most repetitive part of the
response.
"""

from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class Deliverable(BaseModel):
    description: str
    mandatory: bool
    effortEstimateWeeks: Optional[float] = None


class Criterion(BaseModel):
    criterion: str
    weightPercent: Optional[float] = None


class ComplianceItem(BaseModel):
    item: str
    status: Literal["MET", "GAP", "REVIEW"]
    reason: str
    evidence: Optional[str] = None


class KeyDatesBudget(BaseModel):
    submissionDeadline: Optional[str] = None
    submissionDeadlineISO: Optional[str] = None
    contractValueUSD: Optional[float] = None
    paymentTermsDays: Optional[int] = None
    insuranceAmountUSD: Optional[float] = None
    bondRequired: Optional[bool] = None
    bondDetails: Optional[str] = None


class Risk(BaseModel):
    risk: str
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    note: str


class Strength(BaseModel):
    point: str
    note: str


class Verdict(BaseModel):
    score: int
    tag: Literal["GO", "CONDITIONAL", "NO-GO"]
    summary: str


class RFPAnalysis(BaseModel):
    verdict: Verdict
    deliverables: List[Deliverable] = Field(default_factory=list)
    evaluationCriteria: List[Criterion] = Field(default_factory=list)
    compliance: List[ComplianceItem] = Field(default_factory=list)
    keyDatesBudget: KeyDatesBudget = Field(default_factory=KeyDatesBudget)
    risks: List[Risk] = Field(default_factory=list)
    strengths: List[Strength] = Field(default_factory=list)
