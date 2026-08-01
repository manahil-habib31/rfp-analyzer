"""
insights.py

Deterministic, ZERO-API-COST enhancements computed directly from an existing
analysis result (ai_engine.analyze_rfp()'s output) — pure Python post-
processing, no additional LLM calls. Deliberately kept this way: quota has
run out on two providers already this project, so anything that adds real
value without costing another API call is worth prioritizing over more AI
calls.
"""

from datetime import datetime, date


def get_clarification_questions(compliance: list) -> list:
    """
    Turns every checklist item flagged gapType="Requires Clarification" into
    an actual question that can be copied straight into an email/portal
    message to the client — instead of leaving it as a passive "REVIEW"
    badge the proposal team has to notice and act on themselves.
    Returns a list of {"item": str, "question": str}.
    """
    questions = []
    for item in compliance or []:
        if item.get("gapType") == "Requires Clarification":
            base = item.get("question") or item.get("item", "")
            q = base if base.strip().endswith("?") else f"Could you please clarify: {base}?"
            questions.append({"item": item.get("item", ""), "question": q})
    return questions


def format_clarification_email(questions: list, rfp_identifier: str = "") -> str:
    """Formats the clarification questions as a ready-to-send email body."""
    if not questions:
        return ""
    lines = [
        f"Subject: Clarification Questions — {rfp_identifier or 'RFP Submission'}",
        "",
        "Hello,",
        "",
        "Before finalizing our proposal, we would appreciate clarification on the following:",
        "",
    ]
    for i, q in enumerate(questions, start=1):
        lines.append(f"{i}. {q['question']}")
    lines += ["", "Thank you,", "[Your Name]"]
    return "\n".join(lines)


def get_deadline_urgency(submission_deadline_iso: str) -> dict:
    """
    Returns {"days_left": int|None, "label": str, "color": str} based on
    today's date vs. the already-extracted deadline. Pure date arithmetic —
    the deadline itself was already extracted by the core analysis call, so
    this adds zero new API cost.
    """
    if not submission_deadline_iso:
        return {"days_left": None, "label": "Deadline not extracted from this RFP", "color": "#888888"}
    try:
        deadline = datetime.strptime(submission_deadline_iso, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {"days_left": None, "label": "Could not parse the extracted deadline date", "color": "#888888"}

    days_left = (deadline - date.today()).days
    if days_left < 0:
        return {"days_left": days_left, "label": f"\u26A0\uFE0F Deadline passed ({abs(days_left)} day(s) ago)", "color": "#d6453d"}
    elif days_left <= 3:
        return {"days_left": days_left, "label": f"\U0001F534 Only {days_left} day(s) left!", "color": "#d6453d"}
    elif days_left <= 10:
        return {"days_left": days_left, "label": f"\U0001F7E1 {days_left} days remaining", "color": "#b7791f"}
    else:
        return {"days_left": days_left, "label": f"\U0001F7E2 {days_left} days remaining", "color": "#1f9d6b"}


def get_readiness_status(compliance: list) -> dict:
    """
    A deterministic "ready to submit?" gate based on outstanding NO-GO and
    Requires-Clarification items — catches an incomplete submission before
    it goes out, without needing another AI judgment call.
    """
    no_go_items = [c.get("item", "") for c in (compliance or []) if c.get("status") == "NO-GO"]
    clarification_items = [c.get("item", "") for c in (compliance or []) if c.get("gapType") == "Requires Clarification"]
    partial_items = [c.get("item", "") for c in (compliance or []) if c.get("gapType") == "Partially Matched"]

    blocking = no_go_items + clarification_items
    if blocking:
        return {
            "ready": False,
            "label": f"\u26A0\uFE0F Not ready — {len(blocking)} item(s) still need resolution before submission",
            "blocking_items": blocking,
            "color": "#d6453d",
        }
    elif partial_items:
        return {
            "ready": True,
            "label": f"\u2705 Ready for final review — {len(partial_items)} item(s) partially matched, worth a second look",
            "blocking_items": [],
            "color": "#b7791f",
        }
    else:
        return {
            "ready": True,
            "label": "\u2705 All compliance items resolved — ready for final review",
            "blocking_items": [],
            "color": "#1f9d6b",
        }


def get_executive_summary(analysis: dict) -> str:
    """
    A one-line, template-based summary for a non-technical stakeholder —
    built entirely from data the analysis already produced (verdict, top
    risk, compliance score). No AI call: just string formatting.
    """
    verdict = analysis.get("verdict", {}) or {}
    tag = verdict.get("tag", "REVIEW")
    score = verdict.get("score", "\u2014")

    dept_scores = analysis.get("departmentScores", {}) or {}
    overall_pct = (dept_scores.get("overall", {}) or {}).get("score")

    risks = analysis.get("risks", []) or []
    severity_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    top_risk = max(risks, key=lambda r: severity_rank.get(r.get("severity"), 0)) if risks else None

    parts = [f"{tag} — {score}/100."]
    if top_risk:
        parts.append(f"Main risk: {top_risk.get('risk', '')}.")
    if overall_pct is not None:
        parts.append(f"{overall_pct:.0f}% compliance ready.")
    return " ".join(parts)


def get_effort_rollup(analysis: dict) -> str:
    """
    A one-line rollup of estimated prep effort, from totals
    ai_engine.compute_deliverable_totals() already computed — no new call.
    """
    deliverables = analysis.get("deliverables", []) or []
    totals = analysis.get("deliverableTotals", {}) or {}
    total_days = totals.get("totalDays", 0)
    questions = analysis.get("questions", []) or []
    return (
        f"Estimated prep effort: {total_days} person-day(s) across {len(deliverables)} "
        f"deliverable(s), {len(questions)} open question(s) to answer."
    )
