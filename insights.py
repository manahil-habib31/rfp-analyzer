"""
insights.py

Deterministic, ZERO-API-COST enhancements computed directly from an existing
analysis result (ai_engine.analyze_rfp()'s output) — pure Python post-
processing, no additional LLM calls. Deliberately kept this way: quota has
run out on multiple providers already this project, so anything that adds
real value without costing another API call is worth prioritizing over more
AI calls.

Covers: clarification-question extraction, deadline urgency, a submission
readiness gate, a one-line executive summary, an effort rollup, data sanity
checks (catching likely AI extraction mistakes), a time-saved estimate, and
applying human status overrides on top of the AI's compliance checklist.
"""

import re
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
    it goes out, without needing another AI judgment call. Reads whatever
    compliance list it's given, so it automatically reflects human overrides
    when called with the overridden list (see apply_status_overrides below).
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


def get_sanity_warnings(analysis: dict) -> list:
    """
    Catches likely AI extraction mistakes before they mislead the user —
    pure sanity checks on data the analysis already produced, no new API
    call. This is a quality-control layer, not another AI judgment.
    Returns a list of human-readable warning strings (empty if nothing looks off).
    """
    warnings = []
    kdb = analysis.get("keyDatesBudget", {}) or {}

    deadline_iso = kdb.get("submissionDeadlineISO")
    if deadline_iso:
        try:
            deadline = datetime.strptime(deadline_iso, "%Y-%m-%d").date()
            if deadline < date.today():
                warnings.append(
                    f"Extracted submission deadline ({deadline_iso}) is already in the past — "
                    "double-check this was read correctly from the RFP."
                )
        except (ValueError, TypeError):
            warnings.append(f"Extracted submission deadline ('{deadline_iso}') isn't a valid date — worth checking manually.")

    for d in analysis.get("deliverables", []) or []:
        days = d.get("estimatedDays")
        if days is not None and days < 0:
            warnings.append(f"Deliverable \"{d.get('description', '')}\" has a negative estimated effort ({days} days) — likely an extraction error.")

    compliance = analysis.get("compliance", []) or []
    if compliance and len(compliance) != 35:
        warnings.append(f"Compliance checklist has {len(compliance)} items instead of the expected 35 — some items may be missing.")

    contract_value = kdb.get("contractValueUSD")
    if contract_value is not None and contract_value < 0:
        warnings.append(f"Extracted contract value (${contract_value:,.0f}) is negative — likely an extraction error.")

    return warnings


def _extract_max_page(analysis: dict) -> int:
    """Scans every pageRef already extracted across compliance/deliverables/
    questions and returns the highest page number seen — a genuine, if rough,
    proxy for how long the RFP actually is, without needing the raw RFP text
    (which isn't stored in the analysis dict itself)."""
    page_numbers = []

    def _try_parse(page_ref):
        if not page_ref:
            return
        m = re.search(r"\d+", str(page_ref))
        if m:
            page_numbers.append(int(m.group()))

    for c in analysis.get("compliance", []) or []:
        _try_parse(c.get("pageRef"))
    for d in analysis.get("deliverables", []) or []:
        for p in d.get("points", []) or []:
            _try_parse(p.get("pageRef") if isinstance(p, dict) else None)
    for q in analysis.get("questions", []) or []:
        _try_parse(q.get("pageRef"))

    return max(page_numbers) if page_numbers else 10  # a reasonable default if no page refs were found


def get_time_saved_estimate(analysis: dict, analysis_duration_seconds) -> str:
    """
    Turns the tool's speed into a concrete, quotable comparison for a demo —
    not a coding trick, a value statement. The manual-review estimate is a
    transparent, labeled heuristic (5 minutes per page, page count inferred
    from the highest page reference already cited in the analysis), not a
    precise measurement.
    """
    if not analysis_duration_seconds:
        return "Analysis time not recorded for this run."

    estimated_pages = _extract_max_page(analysis)
    manual_minutes = estimated_pages * 5
    manual_hours = manual_minutes / 60
    analysis_minutes = analysis_duration_seconds / 60

    manual_str = f"~{manual_hours:.1f} hour(s)" if manual_hours >= 1 else f"~{manual_minutes:.0f} minute(s)"
    actual_str = f"{analysis_duration_seconds:.0f} seconds" if analysis_duration_seconds < 60 else f"{analysis_minutes:.1f} minutes"

    return f"Estimated manual review: {manual_str} (~{estimated_pages} pages) vs. this analysis: {actual_str}."


def apply_status_overrides(compliance: list, overrides: dict) -> list:
    """
    Applies human corrections (a {item_name: new_status} dict) on top of the
    AI's compliance checklist. Returns a NEW list (the original is left
    untouched) with matching items' status replaced and an "overridden" flag
    set, so the UI can badge them — every downstream read of the returned
    list (readiness gate, metrics, exports) sees the corrected status
    automatically, with no separate sync step needed.
    """
    result = []
    for item in compliance or []:
        item_copy = dict(item)
        name = item_copy.get("item")
        if name in (overrides or {}):
            item_copy["status"] = overrides[name]
            item_copy["overridden"] = True
        result.append(item_copy)
    return result
