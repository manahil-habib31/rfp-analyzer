"""
ai_engine.py

Sends the RFP text + company profile to Gemini and gets back a strict JSON
analysis: verdict, deliverables, evaluation criteria, a checklist-item-by-item
compliance breakdown, key dates/budget, and a risk assessment.

Design notes (matching the pattern used elsewhere in SPS's internship
projects):
- Structured JSON output (response_mime_type + response_schema) rather than
  free-text parsing, so scores/counts/badges are computed, not guessed.
- Retry with exponential backoff on transient errors (429 rate limit, 500/503
  server errors). Daily quota exhaustion is detected separately from a plain
  rate limit and fails fast with a clear message, since retrying a daily cap
  wastes time.

Uses the current `google-genai` SDK (the older `google-generativeai` package
used in some reference projects is deprecated and no longer receives updates).
"""

import json
import os
import time

from google import genai
from google.genai import types, errors

from checklist_items import CHECKLIST_ITEMS, CATEGORY_ORDER, CATEGORY_META
from schemas import RFPAnalysis, RFPCoreAnalysis, ComplianceChecklist, build_category_checklist_schema, ProposalOutline
from decision_rules import apply_hard_rules
from scoring import compute_scores, compute_deliverable_totals, compute_final_verdict

MODEL_NAME = "gemini-2.5-flash"
MAX_RETRIES = 4
INITIAL_BACKOFF_SECONDS = 2


class QuotaExhaustedError(Exception):
    """Raised when Gemini's daily free-tier quota is used up. Not retryable."""
    pass


class AnalysisError(Exception):
    """Raised for any other unrecoverable analysis failure."""
    pass


def _build_core_system_prompt(company_profile: dict) -> str:
    profile_lines = "\n".join(f"- {k}: {v}" for k, v in company_profile.items())
    return f"""You are an RFP capture assistant. You read an incoming RFP and produce a high-level
qualification assessment for a Proposal Capture Manager, weighing it against the company's
profile below.

COMPANY PROFILE (use this to judge fit, not generic assumptions):
{profile_lines}

Produce:
- A "verdict" with THREE separate sub-scores (each 0-100) plus a narrative summary — do NOT
  produce one single overall score; that gets computed afterward from these components plus
  the compliance checklist, so a person can see exactly what's driving the result:
  - "strategicFit": {{"score", "note"}} — how well the RFP's scope matches the company's
    actual stated services/capabilities (from the profile above). Score this on genuine
    alignment, not effort or possibility — a company COULD attempt unfamiliar work, but score
    low if it's a real stretch from what the profile says the company does.
  - "financialTermsFit": {{"score", "note"}} — how favorable the payment terms, insurance
    requirement, bonding requirement, and budget/contract value are relative to the profile's
    stated thresholds and capacity.
  - "riskLevel": {{"score", "note"}} — 100 = very low risk, 0 = very high risk. Weigh
    deadline pressure, ambiguity in scope, competitive/legal exposure, etc.
  - "summary": 2-4 sentences synthesizing all of the above for a Proposal Capture Manager.
- "deliverables": a flat list of parent deliverables — do NOT group or tag these by
  department. Scan the ENTIRE RFP thoroughly for every distinct document, form,
  submission, or artifact the vendor must provide (cover letter, references, insurance
  certificate, technical narrative, pricing sheets, certifications, etc.) — RFPs commonly
  have 6-12+ of these. Each deliverable has:
    - "description": the deliverable's name/title (e.g. "Insurance Documentation",
      "Technical Proposal", "Signed Certifications").
    - "mandatory": true/false — false if optional/nice-to-have.
    - "estimatedDays": your best-effort estimate of effort in days, or null.
    - "priority": "High", "Medium", or "Low" — how critical this deliverable is to a
      successful, compliant submission.
    - "points": 2-6 child items — the specific requirements or description details that
      belong under this deliverable, grounded in the RFP text. Each point has:
        - "point": the requirement/description itself (e.g. "Certificate of insurance
          required", "Coverage of at least $5,000,000").
        - "sectionRef": the RFP section/clause this came from, if named or numbered in
          the text (e.g. "Section 4.2", "Attachment C"). Set to null if the RFP doesn't
          label sections or you can't tell.
        - "pageRef": the page number, if you can tell — the RFP text below is marked with
          "--- Page N ---" headers; cite the page the relevant text appeared under (e.g.
          "Page 3"). Set to null rather than guessing if you can't tell.
      Every deliverable must have at least one point.
- "evaluationCriteria": [{{"criterion", "weightPercent"}}], ordered by weight descending.
- "keyDatesBudget": {{"submissionDeadline", "submissionDeadlineISO" (YYYY-MM-DD or null),
  "contractValueUSD" (number or null), "paymentTermsDays" (number or null),
  "insuranceAmountUSD" (number or null), "bondRequired" (true/false/null), "bondDetails"}}.
- "risks": 3-6 entries, each {{"risk", "severity": "HIGH"|"MEDIUM"|"LOW", "note"}},
  covering the most significant reasons to hesitate on this bid.
- "strengths": 3-6 entries, each {{"point", "note"}}, covering the most significant reasons
  TO pursue this bid — favorable terms, strong capability alignment, relationship value, etc.

Respond with ONLY a raw JSON object (no commentary, no markdown fences)."""


def _build_compliance_system_prompt(company_profile: dict, category: str) -> str:
    cat_items = [it for it in CHECKLIST_ITEMS if it["category"] == category]
    item_list = "\n".join(
        f"{i+1}. {it['item']} — {it['question']}"
        for i, it in enumerate(cat_items)
    )
    profile_lines = "\n".join(f"- {k}: {v}" for k, v in company_profile.items())
    cat_title = CATEGORY_META[category]["title"]

    return f"""You are an RFP compliance assistant. Your ONLY job is to answer a fixed checklist
of {len(cat_items)} {cat_title} items against the RFP text below — nothing else. This is the
entire task; do not summarize the RFP, do not skip items, do not stop early.

COMPANY PROFILE (use this to judge fit, not generic assumptions):
{profile_lines}

CHECKLIST — you MUST answer every single one of these {len(cat_items)} items, in this
exact order, with the exact item name given (do not paraphrase or rename):
{item_list}

For EACH item, decide:
- "status": "GO" (requirement is satisfied or favorable given the company profile),
  "NO-GO" (requirement is not satisfied, or a hard threshold is exceeded), or
  "REVIEW" (needs a human judgment call, or the RFP doesn't provide enough detail).
- Hard rule for "Payment Terms": NET30 or better -> GO. Worse than NET30 -> NO-GO.
- Hard rule for "Insurance Requirements": required coverage <= the company's
  max_insurance_available_usd -> GO. Above it -> NO-GO.
- "reason": one or two sentences grounded in the RFP text. If the RFP doesn't mention this
  item at all, say so plainly (e.g. "Not addressed in the RFP") rather than leaving it out.
- "evidence": a short direct quote or close paraphrase from the RFP. If the RFP genuinely
  doesn't address the item, set evidence to null.
- "pageRef": the page number the evidence came from, if you can tell — the RFP text below
  is marked with "--- Page N ---" headers; cite the page the relevant text appeared under
  (e.g. "Page 3"). If you can't tell, set pageRef to null rather than guessing.

It is critical that your response contains all {len(cat_items)} items — a response with
fewer items is invalid."""


def _is_daily_quota_error(err: errors.APIError) -> bool:
    # The "per day" indicator lives inside the nested quotaId (e.g.
    # "GenerateRequestsPerDayPerProjectPerModel-FreeTier"), not in the short
    # .message field — checking .message alone (as a prior version of this
    # function did) misses it entirely, since Gemini's top-level message text
    # for this error never actually says "day". Check the full details too.
    full_text = f"{getattr(err, 'message', '')} {getattr(err, 'details', '')}".lower()
    return "quota" in full_text and ("perday" in full_text or "per day" in full_text or "daily" in full_text)


def _call_gemini_with_retry(client, system_prompt: str, rfp_text: str, response_schema, max_output_tokens: int = 8192):
    """Generic Gemini caller with retry/backoff, reused for both the core
    analysis call and the dedicated compliance call. Returns the parsed
    object (typed per response_schema) or, if schema validation didn't
    populate .parsed, the raw response text as a fallback.
    Raises QuotaExhaustedError or AnalysisError."""
    backoff = INITIAL_BACKOFF_SECONDS
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=[{"role": "user", "parts": [{"text": "RFP TEXT:\n\n" + rfp_text[:16000]}]}],
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    max_output_tokens=max_output_tokens,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                    temperature=0.1,
                ),
            )
            if getattr(response, "parsed", None) is not None:
                return response.parsed
            text = (response.text or "").strip()
            if not text:
                raise AnalysisError("Gemini returned an empty response.")
            return text

        except errors.ClientError as e:
            if getattr(e, "code", None) == 429:
                if _is_daily_quota_error(e):
                    raise QuotaExhaustedError(
                        "Gemini's daily free-tier quota is used up for this API key/project. "
                        "It resets at midnight Pacific time — try again tomorrow, or enable "
                        "billing on the Google Cloud project to lift the cap."
                    ) from e
                last_error = e
                time.sleep(backoff)
                backoff *= 2
                continue
            raise AnalysisError(f"Gemini rejected the request: {e}") from e

        except errors.ServerError as e:
            last_error = e
            time.sleep(backoff)
            backoff *= 2
            continue

        except errors.APIError as e:
            raise AnalysisError(f"Gemini API error: {e}") from e

    raise AnalysisError(
        f"Gemini kept failing after {MAX_RETRIES} attempts (transient errors). "
        f"Last error: {last_error}"
    )


def _parse_fallback_json(result, label: str) -> dict:
    """Defensive raw-JSON parse for when schema validation didn't populate
    .parsed (result is a raw string in that case)."""
    cleaned = result.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        preview = cleaned[:300].replace("\n", " ")
        raise AnalysisError(
            f"Could not parse Gemini's {label} response as JSON: {e}. Got: \"{preview}...\""
        ) from e


def _build_outline_prompt(company_profile: dict) -> str:
    return """You are a proposal planning assistant. Based on the RFP text below, produce a
numbered outline for the PROPOSAL RESPONSE DOCUMENT itself (not a summary of the RFP) —
the actual table of contents SPS would submit back to the client.

Structure it as top-level sections (e.g. "Technical Proposal", "Financial Proposal",
"Compliance & Administrative Documentation" — adapt these to what this specific RFP
actually asks for), each containing an ordered list of sub-section titles that respond
to what the RFP requires (e.g. "Cover Page", "Response to Scope of Services", "Ownership
Details", "References", "Insurance Documentation", "Pricing Schedule", "Signed Certifications").

Only include sections and sub-sections that are actually relevant to submitting a proposal
in response to THIS RFP — ground it in the RFP's actual submission requirements, evaluation
criteria, and required attachments/forms, not a generic template.

Respond with ONLY a raw JSON object (no commentary, no markdown fences) matching:
{
  "sections": [
    {"title": "string", "children": [{"title": "string"}, ...]},
    ...
  ]
}
Do not include section/sub-section numbers in the titles — numbering is added separately."""


def _apply_outline_numbering(outline: dict) -> dict:
    """Computes 1, 1.1, 1.2, 2, 2.1, ... numbering in code, rather than
    trusting the model to number correctly — guarantees the numbering is
    always sequential and never duplicated or out of order."""
    numbered_sections = []
    for i, section in enumerate(outline.get("sections", []), start=1):
        children = []
        for j, child in enumerate(section.get("children", []), start=1):
            children.append({"number": f"{i}.{j}", "title": child.get("title", "")})
        numbered_sections.append({
            "number": str(i),
            "title": section.get("title", ""),
            "children": children,
        })
    return {"sections": numbered_sections}


def generate_proposal_outline(rfp_text: str, company_profile: dict, api_key: str) -> dict:
    """Stage 3 (Proposal Planning): generates a numbered parent/child outline
    of the proposal response document itself. Kept as its own call/function
    (not folded silently into analyze_rfp's internals) so it can be invoked,
    tested, or reused independently."""
    if not api_key:
        raise AnalysisError("No Gemini API key configured.")
    client = genai.Client(api_key=api_key)
    prompt = _build_outline_prompt(company_profile)
    result = _call_gemini_with_retry(client, prompt, rfp_text, ProposalOutline, max_output_tokens=2048)
    if isinstance(result, ProposalOutline):
        outline = result.model_dump()
    else:
        outline = _parse_fallback_json(result, "proposal outline")
    return _apply_outline_numbering(outline)


def analyze_rfp(rfp_text: str, company_profile: dict, api_key: str) -> dict:
    """
    Runs the full analysis as multiple Gemini calls:
      1. Core analysis (verdict, deliverables, criteria, dates/budget, risks, strengths).
      2. The compliance checklist, split into one call PER DEPARTMENT (Financial,
         Legal, Operations, Technical) rather than one call for all 35 items.
         Gemini's controlled generation rejects a single schema requiring an
         exact-length array of 35 complex nested objects ("too many states for
         serving") — splitting into 4 smaller exact-length arrays (6/13/11/5
         items) keeps each call's constraint grammar small enough to serve,
         while still guaranteeing an exact item count per department.
    If any individual department call fails, the others still proceed — a
    single failed department degrades to "REVIEW — not returned" for just
    that department's items rather than failing the whole analysis.
    Results are merged back onto the fixed checklist (so the report always
    covers exactly the right items regardless of ordering) and the
    deterministic hard-rule overrides are applied on top.
    """
    if not api_key:
        raise AnalysisError("No Gemini API key configured.")

    client = genai.Client(api_key=api_key)

    # --- Call 1: core analysis ---
    core_prompt = _build_core_system_prompt(company_profile)
    core_result = _call_gemini_with_retry(client, core_prompt, rfp_text, RFPCoreAnalysis, max_output_tokens=4096)
    if isinstance(core_result, RFPCoreAnalysis):
        data = core_result.model_dump()
    else:
        data = _parse_fallback_json(core_result, "core analysis")

    # --- Calls 2-5: compliance checklist, one call per department ---
    all_raw_items = []
    compliance_errors = []
    for category in CATEGORY_ORDER:
        cat_count = len([it for it in CHECKLIST_ITEMS if it["category"] == category])
        if cat_count == 0:
            continue
        try:
            prompt = _build_compliance_system_prompt(company_profile, category)
            schema = build_category_checklist_schema(cat_count)
            result = _call_gemini_with_retry(client, prompt, rfp_text, schema, max_output_tokens=4096)
            if hasattr(result, "model_dump"):
                all_raw_items.extend(result.model_dump()["items"])
            else:
                parsed = _parse_fallback_json(result, f"{category} checklist")
                all_raw_items.extend(parsed.get("items", parsed if isinstance(parsed, list) else []))
        except (QuotaExhaustedError, AnalysisError) as e:
            # Don't let one department's failure take down the whole analysis —
            # record it and let _merge_compliance fill those items with the
            # "not returned" placeholder so the rest of the report still works.
            compliance_errors.append(f"{CATEGORY_META[category]['title']}: {e}")

    data["compliance"] = _merge_compliance(all_raw_items)
    if compliance_errors:
        data["complianceWarnings"] = compliance_errors
    data["departmentScores"] = compute_scores(data["compliance"])

    # Blend the AI's three sub-scores with the deterministic compliance score
    # into one visible, weighted Fit Score — replaces the raw VerdictComponents
    # the core call returned with a flat {score, tag, summary, breakdown} dict.
    data["verdict"] = compute_final_verdict(
        data["verdict"], data["departmentScores"]["overall"]["score"]
    )
    # Hard-rule overrides (payment terms, insurance) run AFTER the blend, since
    # they're policy, not opinion, and must be able to override the blended
    # tag regardless of what the weighted score came out to.
    data = apply_hard_rules(data, company_profile)

    data["deliverableTotals"] = compute_deliverable_totals(data.get("deliverables", []))

    # --- Call 6: proposal outline (Stage 3 planning) ---
    try:
        data["proposalOutline"] = generate_proposal_outline(rfp_text, company_profile, api_key)
    except (QuotaExhaustedError, AnalysisError) as e:
        data["proposalOutline"] = {"sections": []}
        data["outlineWarning"] = str(e)

    return data


def _merge_compliance(ai_items: list) -> list:
    by_name = {}
    for it in ai_items or []:
        name = (it or {}).get("item")
        if name:
            by_name[name.strip().lower()] = it

    merged = []
    for ci in CHECKLIST_ITEMS:
        found = by_name.get(ci["item"].strip().lower())
        merged.append({
            "category": ci["category"],
            "item": ci["item"],
            "question": ci["question"],
            "status": (found or {}).get("status", "REVIEW"),
            "reason": (found or {}).get("reason", "Not returned by the model — re-run the analysis or check this item manually."),
            "evidence": (found or {}).get("evidence"),
            "pageRef": (found or {}).get("pageRef"),
        })
    return merged
