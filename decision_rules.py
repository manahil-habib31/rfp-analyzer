"""
decision_rules.py

Deterministic, code-level GO/NO-GO threshold rules — the same pattern used
elsewhere for this kind of RFP screening: compare an extracted number against
a configured limit, and let that comparison decide the outcome, rather than
trusting the model's own judgment on a rule that's already fixed by policy.

This covers the two hard rules that are explicitly spelled out in SPS's
checklist:
  - Payment Terms: NET30 (or better) -> GO. Worse than NET30 -> escalate to
    Accounting.
  - Insurance Requirements: <= the company's available coverage -> GO.
    Above it -> NO-GO.

Both thresholds are read from the company profile (not hard-coded), so
they're configurable per the sidebar rather than fixed in code — a company
with different policy limits just edits the profile, no code changes needed.

SPLIT INTO TWO PHASES (this used to be one function, apply_hard_rules,
called after department scores were already computed):
  1. correct_compliance_items() — overwrites the "Payment Terms" and
     "Insurance Requirements" checklist rows with a rule-based status/
     reason. Must run BEFORE scoring.compute_scores(), not after — running
     it after (the original bug) meant the department score breakdown and
     its "fails for: X, Y, Z" summary text got computed from the AI's raw,
     pre-correction judgment, while the checklist table shown right below
     it displayed the corrected status. Both come from the same
     data["compliance"] list, so a viewer would see the checklist row say
     "Payment Terms: GO" while the department summary above it says
     "fails for: Payment Terms" — a direct, visible contradiction, and
     exactly the kind of thing that erodes trust in an audit tool.
  2. apply_verdict_tag_override() — forces the FINAL blended verdict tag/
     score if either threshold was breached. This one genuinely does need
     to run AFTER ai_engine.compute_final_verdict(), since it overrides
     that blended {tag, score, summary} shape, which doesn't exist yet
     any earlier in the pipeline.

apply_hard_rules() is kept as a combined convenience wrapper (calls both
phases back to back) for any caller that doesn't care about the ordering
subtlety — but ai_engine.py's analyze_rfp() calls the two phases
separately, at the two different correct points in its pipeline.
"""


def _find_item(compliance_list: list, item_name: str):
    for it in compliance_list:
        if it.get("item") == item_name:
            return it
    return None


def correct_compliance_items(data: dict, company_profile: dict) -> dict:
    """Phase 1: corrects the Payment Terms / Insurance Requirements checklist
    rows in data["compliance"] against the company profile's thresholds.
    Stores which forced tag (if any) this implies in
    data["_forced_verdict_tag"] for apply_verdict_tag_override() to pick up
    later — does NOT touch data["verdict"] itself, since at this point in
    the pipeline the blended verdict doesn't exist yet."""
    kdb = data.get("keyDatesBudget", {}) or {}
    compliance = data.get("compliance", []) or []

    forced_tag = None  # set if a hard threshold breach must override the AI's overall tag

    # --- Rule 1: Payment Terms vs acceptable_payment_terms_days ---
    payment_days = kdb.get("paymentTermsDays")
    acceptable_days = company_profile.get("acceptable_payment_terms_days", 30)
    payment_item = _find_item(compliance, "Payment Terms")
    if payment_item is not None:
        if payment_days is None:
            payment_item["status"] = "REVIEW"
            payment_item["reason"] = (
                "Payment terms aren't clearly stated in the RFP — confirm manually before proceeding."
            )
        elif payment_days <= acceptable_days:
            payment_item["status"] = "GO"
            payment_item["reason"] = (
                f"NET{payment_days} is within the acceptable NET{acceptable_days} threshold — GO."
            )
            payment_item["evidence"] = f"RFP states payment terms of NET{payment_days}."
        else:
            payment_item["status"] = "NO-GO"
            payment_item["reason"] = (
                f"NET{payment_days} exceeds the acceptable NET{acceptable_days} threshold — escalate to Accounting."
            )
            payment_item["evidence"] = f"RFP states payment terms of NET{payment_days}."
            forced_tag = forced_tag or "CONDITIONAL"

    # --- Rule 2: Insurance Requirements vs max_insurance_available_usd ---
    insurance_amount = kdb.get("insuranceAmountUSD")
    max_insurance = company_profile.get("max_insurance_available_usd", 5_000_000)
    insurance_item = _find_item(compliance, "Insurance Requirements")
    if insurance_item is not None:
        if insurance_amount is None:
            insurance_item["status"] = "REVIEW"
            insurance_item["reason"] = (
                "Insurance requirement isn't clearly stated in the RFP — confirm manually before proceeding."
            )
        elif insurance_amount <= max_insurance:
            insurance_item["status"] = "GO"
            insurance_item["reason"] = (
                f"${insurance_amount:,.0f} required is within the ${max_insurance:,.0f} available coverage — GO."
            )
            insurance_item["evidence"] = f"RFP states required insurance coverage of ${insurance_amount:,.0f}."
        else:
            insurance_item["status"] = "NO-GO"
            insurance_item["reason"] = (
                f"${insurance_amount:,.0f} required exceeds the ${max_insurance:,.0f} available coverage — NO-GO."
            )
            insurance_item["evidence"] = f"RFP states required insurance coverage of ${insurance_amount:,.0f}."
            forced_tag = "NO-GO"  # insurance breach is the harder rule, takes priority over a payment-terms escalation

    data["compliance"] = compliance
    forced_tag = _catch_missed_insurance_nogo(data, forced_tag, insurance_item)
    data["_forced_verdict_tag"] = forced_tag
    return data


def _catch_missed_insurance_nogo(data: dict, forced_tag, insurance_item=None):
    """Safety net for exactly the gap that produced a wrong GO verdict on
    26-ODU-30-JNH: the Legal-category checklist item ("Insurance Coverage")
    correctly found "$1,000,000 combined single limits" directly in the RFP
    text and flagged NO-GO — but Rule 2 above only reads
    keyDatesBudget.insuranceAmountUSD, which came back empty from a
    DIFFERENT AI call over the same document. Because that field was empty,
    Rule 2 fell through to REVIEW instead of NO-GO, and the real NO-GO
    signal the model already found elsewhere in the checklist never reached
    the top-level verdict.

    This scans every OTHER compliance item (any department, not just the
    one named "Insurance Requirements") for anything with "insurance" in
    its name that the AI itself already marked NO-GO, and — if Rule 2
    hasn't already forced NO-GO — escalates the verdict using that item's
    own evidence. It leaves the OTHER item's own status/reason untouched
    (that one's already correct), but DOES update the primary
    "Insurance Requirements" row to cross-reference it — otherwise the
    verdict would say NO-GO while that row still says REVIEW, exactly the
    same kind of visible self-contradiction the phase-1/phase-2 split above
    was designed to prevent for Payment Terms/Insurance in the first
    place."""
    if forced_tag == "NO-GO":
        return forced_tag
    for item in data.get("compliance", []) or []:
        name = (item.get("item") or "").lower()
        if "insurance" in name and item.get("status") == "NO-GO":
            if insurance_item is not None and insurance_item is not item:
                insurance_item["status"] = "NO-GO"
                insurance_item["reason"] = (
                    f"No insurance dollar amount was extracted for this rule directly, but "
                    f"'{item.get('item')}' elsewhere in the checklist found and flagged this "
                    f"as NO-GO from the RFP text — escalating here too rather than leaving "
                    f"this row as REVIEW while the overall verdict reflects NO-GO."
                )
                insurance_item["evidence"] = item.get("evidence")
            return "NO-GO"
    return forced_tag


def apply_verdict_tag_override(data: dict) -> dict:
    """Phase 2: forces the FINAL blended verdict tag/score if
    correct_compliance_items() (phase 1, run earlier) flagged a threshold
    breach. Must run after ai_engine.compute_final_verdict() has already
    set data["verdict"] to the blended {tag, score, summary, breakdown}
    shape — reads data["_forced_verdict_tag"] rather than recomputing
    anything, so it can't disagree with what phase 1 already decided."""
    forced_tag = data.pop("_forced_verdict_tag", None)
    verdict = data.get("verdict", {}) or {}

    if forced_tag is not None:
        original_tag = verdict.get("tag")
        should_override = (
            forced_tag == "NO-GO"
            or (forced_tag == "CONDITIONAL" and original_tag == "GO")
        )
        if should_override:
            note = {
                "NO-GO": (
                    "Overall verdict forced to NO-GO: the insurance requirement exceeds the "
                    "company's available coverage threshold (hard policy rule)."
                ),
                "CONDITIONAL": (
                    "Overall verdict adjusted to CONDITIONAL: payment terms exceed the acceptable "
                    "threshold and must be escalated to Accounting (hard policy rule)."
                ),
            }[forced_tag]
            verdict["tag"] = forced_tag
            verdict["summary"] = (verdict.get("summary", "").rstrip(". ") + ". " + note).strip()
            # Keep the numeric score consistent with the forced tag's band, so
            # the displayed number can never contradict the label (e.g. a
            # forced NO-GO showing a 75/100 would look like a bug, not policy).
            score = verdict.get("score")
            if isinstance(score, (int, float)):
                if forced_tag == "NO-GO" and score >= 40:
                    verdict["score"] = 39
                elif forced_tag == "CONDITIONAL" and not (40 <= score <= 69):
                    verdict["score"] = 69 if score >= 70 else 40

    data["verdict"] = verdict
    return data


def apply_hard_rules(data: dict, company_profile: dict) -> dict:
    """Combined convenience wrapper (both phases back to back) — kept for
    any caller that doesn't need the two-phase ordering. ai_engine.py's
    analyze_rfp() does NOT use this; it calls the two phases separately at
    the correct points in its pipeline (see the module docstring above)."""
    data = correct_compliance_items(data, company_profile)
    data = apply_verdict_tag_override(data)
    return data
