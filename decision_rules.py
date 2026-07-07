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

This runs *after* the AI call and checklist merge, as a final deterministic
pass: it overwrites the "Payment Terms" and "Insurance Requirements" checklist
rows with a rule-based status/reason, and adjusts the overall verdict tag if
either threshold is breached, since those two outcomes are policy, not
opinion.
"""


def _find_item(compliance_list: list, item_name: str):
    for it in compliance_list:
        if it.get("item") == item_name:
            return it
    return None


def apply_hard_rules(data: dict, company_profile: dict) -> dict:
    kdb = data.get("keyDatesBudget", {}) or {}
    compliance = data.get("compliance", []) or []
    verdict = data.get("verdict", {}) or {}

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

    data["verdict"] = verdict
    data["compliance"] = compliance
    return data
