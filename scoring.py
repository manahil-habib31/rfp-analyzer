"""
scoring.py

Deterministic per-department (and overall) compliance scoring, computed
directly from the merged checklist results — no extra AI call, so the score
is fully reproducible: the same checklist results always produce the same
score, run to run.

Scoring: each item contributes GO=100, REVIEW=50, NO-GO=0 points; a
department's score is the average across its items. REVIEW is treated as
"half credit" rather than a failure, since it usually means "the RFP just
didn't say" rather than "this is a problem" — that matches how a real
capture manager would read an incomplete-but-not-disqualifying checklist.

Recommendation labels are also rule-based, not AI-judged:
  - any NO-GO in scope           -> "High Risk"
  - no NO-GO, but any REVIEW     -> "Review Needed"
  - everything GO                -> "Proceed"
"""

from checklist_items import CATEGORY_META, CATEGORY_ORDER

POINTS = {"GO": 100, "REVIEW": 50, "NO-GO": 0}


def _recommendation(items: list) -> str:
    statuses = {it.get("status") for it in items}
    if "NO-GO" in statuses:
        return "High Risk"
    if "REVIEW" in statuses:
        return "Review Needed"
    return "Proceed"


def _summary(items: list, recommendation: str, score: float) -> str:
    if recommendation == "Proceed":
        return f"All {len(items)} item(s) in this category are GO."
    flagged = [it["item"] for it in items if it.get("status") in ("REVIEW", "NO-GO")]
    label = "requires review" if recommendation == "Review Needed" else "fails"
    return f"Score is {score:.0f}% — {label} for: " + ", ".join(flagged) + "."


def compute_scores(compliance: list) -> dict:
    """
    Returns:
      {
        "overall": {"score": float, "recommendation": str, "summary": str},
        "byCategory": {
            "financial": {"score": ..., "recommendation": ..., "summary": ...},
            ...
        }
      }
    """
    by_category = {}
    for cat in CATEGORY_ORDER:
        items = [c for c in compliance if c.get("category") == cat]
        if not items:
            continue
        score = sum(POINTS.get(it.get("status"), 50) for it in items) / len(items)
        rec = _recommendation(items)
        by_category[cat] = {
            "score": round(score, 1),
            "recommendation": rec,
            "summary": _summary(items, rec, score),
            "title": CATEGORY_META[cat]["title"],
        }

    if compliance:
        overall_score = sum(POINTS.get(it.get("status"), 50) for it in compliance) / len(compliance)
    else:
        overall_score = 0.0
    overall_rec = _recommendation(compliance)
    overall_summary = _summary(compliance, overall_rec, overall_score) if compliance else "No compliance data available."

    return {
        "overall": {
            "score": round(overall_score, 1),
            "recommendation": overall_rec,
            "summary": overall_summary,
        },
        "byCategory": by_category,
    }


# Weighted blend for the final Fit Score — visible and reproducible, instead
# of one opaque AI-judged number. Three components come from the AI's own
# sub-scores (strategic fit, financial terms, risk); the fourth,
# "complianceReadiness", is NOT re-judged by the AI at all — it's the exact
# same overall compliance percentage already computed from the 35-item
# checklist (compute_scores, above), so the Fit Score can never silently
# contradict the Compliance Checklist tab the way a fully independent AI
# judgment could.
VERDICT_WEIGHTS = {
    "strategicFit": 0.30,
    "financialTermsFit": 0.20,
    "complianceReadiness": 0.30,
    "riskLevel": 0.20,
}


def compute_final_verdict(verdict_components: dict, compliance_overall_score: float) -> dict:
    """
    verdict_components: the AI-supplied {"strategicFit": {"score","note"},
    "financialTermsFit": {...}, "riskLevel": {...}, "summary": str}.
    compliance_overall_score: the 0-100 overall score from compute_scores().

    Returns a dict with the visible breakdown plus the computed total score
    and a threshold-derived tag (GO >= 70, CONDITIONAL 40-69, NO-GO < 40).
    The two deterministic hard rules (payment terms, insurance — see
    decision_rules.py) are applied AFTER this and can still override the tag.
    """
    strategic = verdict_components.get("strategicFit", {}).get("score", 50)
    financial = verdict_components.get("financialTermsFit", {}).get("score", 50)
    risk = verdict_components.get("riskLevel", {}).get("score", 50)

    total = (
        VERDICT_WEIGHTS["strategicFit"] * strategic
        + VERDICT_WEIGHTS["financialTermsFit"] * financial
        + VERDICT_WEIGHTS["complianceReadiness"] * compliance_overall_score
        + VERDICT_WEIGHTS["riskLevel"] * risk
    )
    total = round(total)

    if total >= 70:
        tag = "GO"
    elif total >= 40:
        tag = "CONDITIONAL"
    else:
        tag = "NO-GO"

    breakdown = {
        "strategicFit": {
            "score": strategic, "weightPercent": int(VERDICT_WEIGHTS["strategicFit"] * 100),
            "note": verdict_components.get("strategicFit", {}).get("note", ""),
        },
        "financialTermsFit": {
            "score": financial, "weightPercent": int(VERDICT_WEIGHTS["financialTermsFit"] * 100),
            "note": verdict_components.get("financialTermsFit", {}).get("note", ""),
        },
        "complianceReadiness": {
            "score": round(compliance_overall_score), "weightPercent": int(VERDICT_WEIGHTS["complianceReadiness"] * 100),
            "note": "Computed directly from the compliance checklist's overall score — not separately judged by the AI.",
        },
        "riskLevel": {
            "score": risk, "weightPercent": int(VERDICT_WEIGHTS["riskLevel"] * 100),
            "note": verdict_components.get("riskLevel", {}).get("note", ""),
        },
    }

    return {
        "score": total,
        "tag": tag,
        "summary": verdict_components.get("summary", ""),
        "breakdown": breakdown,
    }


def compute_deliverable_totals(deliverables: list) -> dict:
    """
    Simple aggregate across the flat deliverables list (no department
    grouping): total estimated days and a count by priority. Deterministic —
    computed directly from the deliverables themselves, not separately
    AI-judged.
    """
    from checklist_items import PRIORITY_RANK

    total_days = sum(d.get("estimatedDays") or 0 for d in deliverables)
    by_priority = {"High": 0, "Medium": 0, "Low": 0}
    for d in deliverables:
        p = d.get("priority", "Medium")
        by_priority[p] = by_priority.get(p, 0) + 1
    return {"totalDays": total_days, "byPriority": by_priority}
