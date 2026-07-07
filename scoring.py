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
