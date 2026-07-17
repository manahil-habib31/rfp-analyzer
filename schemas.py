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
from pydantic import BaseModel, Field, create_model

from checklist_items import CHECKLIST_ITEMS

_ITEM_COUNT = len(CHECKLIST_ITEMS)


class DeliverablePoint(BaseModel):
    """A single child requirement/description item under a parent deliverable,
    traceable back to where in the RFP it came from — including which source
    document, when the RFP was uploaded as multiple files (main RFP +
    exhibits/attachments)."""
    point: str
    docRef: Optional[str] = None      # e.g. "RFP_Exhibit_A.pdf"
    sectionRef: Optional[str] = None  # e.g. "Section 4.2 - Insurance Requirements"
    pageRef: Optional[str] = None     # e.g. "Page 3"


class Deliverable(BaseModel):
    """A parent deliverable (e.g. 'Insurance Documentation') expanded into its own
    child requirement/description points (e.g. 'Certificate required', 'Coverage
    $5M', 'Valid through contract period') — a flat two-level list, not grouped
    by department."""
    description: str
    mandatory: bool
    estimatedDays: Optional[int] = None
    priority: Literal["High", "Medium", "Low"] = "Medium"
    points: List[DeliverablePoint] = Field(default_factory=list, min_length=1)


class Criterion(BaseModel):
    criterion: str
    weightPercent: Optional[float] = None


class ComplianceItem(BaseModel):
    item: str
    status: Literal["GO", "NO-GO", "REVIEW"]
    reason: str
    evidence: Optional[str] = None
    docRef: Optional[str] = None  # e.g. "RFP_Exhibit_B.pdf"
    pageRef: Optional[str] = None


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


class ScoreComponent(BaseModel):
    score: int  # 0-100, where 100 is best (for riskLevel: 100 = very low risk)
    note: str


class VerdictComponents(BaseModel):
    """The AI supplies these three sub-scores plus a narrative summary.
    The overall 0-100 score and GO/CONDITIONAL/NO-GO tag are NOT asked of the
    model — they're computed deterministically afterward (see
    ai_engine.compute_final_verdict), blended with the compliance checklist's
    own score, so the final number is a visible, weighted breakdown rather
    than one opaque AI judgment call."""
    strategicFit: ScoreComponent  # capability/service alignment with the RFP's scope
    financialTermsFit: ScoreComponent  # payment terms, insurance, bonding, budget feasibility
    riskLevel: ScoreComponent  # 100 = very low risk, 0 = very high risk
    summary: str


class RFPCoreAnalysis(BaseModel):
    """Everything except the compliance checklist — asked for in its own,
    smaller call so it isn't competing with the 34-item checklist for the
    model's attention/output budget."""
    verdict: VerdictComponents
    rfpIdentifier: Optional[str] = None  # e.g. "26-CMS-114-IAM" or a short title — used for
                                          # display and as the basis for the downloaded report's
                                          # filename, instead of just the first uploaded filename.
    deliverables: List[Deliverable] = Field(default_factory=list)
    evaluationCriteria: List[Criterion] = Field(default_factory=list)
    keyDatesBudget: KeyDatesBudget = Field(default_factory=KeyDatesBudget)
    risks: List[Risk] = Field(default_factory=list)
    strengths: List[Strength] = Field(default_factory=list)


class ComplianceChecklist(BaseModel):
    """The full checklist in one shape — kept for backward-compat/testing,
    but NOT used for the live API call anymore: a fixed length of 35 nested
    objects is too large a constraint grammar for Gemini's controlled
    generation to serve ("too many states" error). See
    build_category_checklist_schema below for the schema actually used."""
    items: List[ComplianceItem] = Field(min_length=_ITEM_COUNT, max_length=_ITEM_COUNT)


def build_category_checklist_schema(count: int):
    """
    Dynamically builds a small, category-scoped checklist schema with an
    exact-length constraint of `count` items. Splitting the 35-item checklist
    into 4 per-department calls (6/13/11/5 items) keeps each call's
    constraint grammar small enough for Gemini to actually serve, while still
    guaranteeing an exact item count per call.
    """
    return create_model(
        f"ComplianceChecklist{count}",
        items=(List[ComplianceItem], Field(min_length=count, max_length=count)),
    )


# Kept for backward compatibility (e.g. dummy-data tests) — combines both
# halves into the same shape the rest of the app (app.py, pdf_report.py)
# already expects.
class RFPAnalysis(BaseModel):
    verdict: VerdictComponents
    rfpIdentifier: Optional[str] = None
    deliverables: List[Deliverable] = Field(default_factory=list)
    evaluationCriteria: List[Criterion] = Field(default_factory=list)
    compliance: List[ComplianceItem] = Field(default_factory=list)
    keyDatesBudget: KeyDatesBudget = Field(default_factory=KeyDatesBudget)
    risks: List[Risk] = Field(default_factory=list)
    strengths: List[Strength] = Field(default_factory=list)


# --- Proposal Outline (Stage 3: Proposal Planning) ---
# Just the parent/child structure for now — a numbered outline of the
# proposal response itself (e.g. "1. Technical Proposal" -> "1.1 Cover Page",
# "1.2 Response to Scope of Services", ...). The AI only supplies titles;
# section/sub-section numbers (1, 1.1, 1.2, 2, 2.1, ...) are computed
# deterministically in code afterward, so numbering can never come back
# wrong, duplicated, or out of order.
class OutlineChild(BaseModel):
    title: str


class OutlineSection(BaseModel):
    title: str
    children: List[OutlineChild] = Field(min_length=1)


class ProposalOutline(BaseModel):
    sections: List[OutlineSection] = Field(min_length=1)
