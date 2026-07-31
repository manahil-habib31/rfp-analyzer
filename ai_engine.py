"""
ai_engine.py

Sends the RFP text + company profile to OpenAI and gets back a strict JSON
analysis: verdict, deliverables, evaluation criteria, a checklist-item-by-item
compliance breakdown, key dates/budget, and a risk assessment.

Design notes (matching the pattern used elsewhere in SPS's internship
projects):
- Structured JSON output (OpenAI's "structured outputs" via the
  `beta.chat.completions.parse` helper + a Pydantic response_format) rather
  than free-text parsing, so scores/counts/badges are computed, not guessed.
- Retry with exponential backoff on transient errors (429 rate limit, 5xx
  server errors). Billing/quota exhaustion is detected separately from a
  plain rate limit and fails fast with a clear message, since retrying a
  quota cap wastes time.

Uses the official `openai` Python SDK (>=1.40, for structured-outputs
support via `.parse()`).
"""

import json
import os
import time

import openai
from openai import OpenAI

from checklist_items import CHECKLIST_ITEMS, CATEGORY_ORDER, CATEGORY_META
from schemas import RFPAnalysis, RFPCoreAnalysis, ComplianceChecklist, build_category_checklist_schema, ProposalOutline, ResponseGenerationResult
from knowledge_base import get_full_knowledge_base
from decision_rules import apply_hard_rules
from scoring import compute_scores, compute_deliverable_totals, compute_final_verdict

# Routed through OpenRouter (https://openrouter.ai), which proxies to OpenAI
# (and many other providers) behind an OpenAI-compatible API — model names
# there are prefixed with the provider, e.g. "openai/gpt-4o-mini".
MODEL_NAME = "openai/gpt-4o-mini"
MAX_RETRIES = 4
INITIAL_BACKOFF_SECONDS = 2

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class QuotaExhaustedError(Exception):
    """Raised when OpenAI's billing/rate quota is used up. Not retryable."""
    pass


class AnalysisError(Exception):
    """Raised for any other unrecoverable analysis failure."""
    pass


def _build_core_system_prompt(company_profile: dict, doc_names: list = None) -> str:
    profile_lines = "\n".join(f"- {k}: {v}" for k, v in company_profile.items())
    doc_note = ""
    if doc_names and len(doc_names) > 1:
        doc_list = "\n".join(f"  - {d}" for d in doc_names)
        doc_note = f"""
This RFP was assembled from {len(doc_names)} separate source documents:
{doc_list}
The text below is prefixed throughout with "--- Document: <filename>, Page N ---" headers
marking which document and page each section came from. Track these headers as you read,
and use them to fill in "docRef" (and "pageRef") accurately for every deliverable point —
do not leave docRef null just because you didn't note which document a point came from.
"""
    return f"""You are an RFP capture assistant. You read an incoming RFP and produce a high-level
qualification assessment for a Proposal Capture Manager, weighing it against the company's
profile below.
{doc_note}
COMPANY PROFILE (use this to judge fit, not generic assumptions):
{profile_lines}

Produce:
- "rfpIdentifier": the RFP's official solicitation/bid number if the document states one
  (e.g. "RFP No. 26-CMS-114-IAM", "IFB BPM057272", "Solicitation #2026-114") — give just the
  number/code itself, not the full sentence. If no number is stated, give a short 4-8 word
  title instead (e.g. "PingOne Advanced Identity Subscription"). Set to null only if you
  genuinely can't identify either from the text.
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
        - "docRef": which source document this came from — the RFP text below is marked
          with "--- Document: <filename>, Page N ---" headers whenever more than one
          document was supplied (e.g. the main RFP plus Exhibit A/B/C); cite the exact
          filename shown in that header (e.g. "RFP_Exhibit_A.pdf"). Set to null if the
          text has no such document markers (a single-document RFP) or you can't tell.
        - "sectionRef": the RFP section/clause this came from, if named or numbered in
          the text (e.g. "Section 4.2", "Attachment C"). Set to null if the RFP doesn't
          label sections or you can't tell.
        - "pageRef": the page number, if you can tell — cite the page number shown in the
          "--- Document: ..., Page N ---" header the relevant text appeared under (e.g.
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
- "questions": every DIRECT question the RFP poses to the vendor — distinct from the fixed
  compliance checklist, since these vary RFP to RFP. Look for phrasing like "Describe your...",
  "Explain your...", "Provide details on...", "How does your company...". Examples: "Describe
  your company's experience with similar projects", "Explain your security approach", "Provide
  pricing information", "Explain your development methodology". Do NOT include the fixed
  checklist-style requirements (those are handled separately) — only genuine open-ended
  questions the vendor must answer in prose. Extract every one found, however many that is;
  do not stop after 3-4. Each has:
    - "question": the question itself, as close to verbatim as reasonable.
    - "docRef": which source document this came from (see the document-marker note above).
      Set to null for a single-document RFP or if you can't tell.
    - "sectionRef": the RFP section/clause this came from, if labeled. Set to null if not.
    - "pageRef": the page number, if you can tell. Set to null rather than guessing.

Respond with ONLY a raw JSON object (no commentary, no markdown fences)."""


def _build_compliance_system_prompt(company_profile: dict, category: str, doc_names: list = None) -> str:
    cat_items = [it for it in CHECKLIST_ITEMS if it["category"] == category]
    item_list = "\n".join(
        f"{i+1}. {it['item']} — {it['question']}"
        for i, it in enumerate(cat_items)
    )
    profile_lines = "\n".join(f"- {k}: {v}" for k, v in company_profile.items())
    cat_title = CATEGORY_META[category]["title"]
    doc_note = ""
    if doc_names and len(doc_names) > 1:
        doc_list = "\n".join(f"  - {d}" for d in doc_names)
        doc_note = f"""
This RFP was assembled from {len(doc_names)} separate source documents:
{doc_list}
The text below is prefixed throughout with "--- Document: <filename>, Page N ---" headers
marking which document and page each section came from. Track these headers as you read,
and use them to fill in "docRef" (and "pageRef") accurately for every checklist item's
evidence — do not leave docRef null just because you didn't note which document it came from.
"""

    return f"""You are an RFP compliance assistant. Your ONLY job is to answer a fixed checklist
of {len(cat_items)} {cat_title} items against the RFP text below — nothing else. This is the
entire task; do not summarize the RFP, do not skip items, do not stop early.
{doc_note}
COMPANY PROFILE (use this to judge fit, not generic assumptions):
{profile_lines}

CHECKLIST — you MUST answer every single one of these {len(cat_items)} items, in this
exact order, with the exact item name given (do not paraphrase or rename):
{item_list}

For EACH item, decide:
- "status": "GO" (requirement is satisfied or favorable given the company profile),
  "NO-GO" (requirement is not satisfied, or a hard threshold is exceeded), or
  "REVIEW" (needs a human judgment call, or the RFP doesn't provide enough detail).
- "gapType": ONLY set this when status is "REVIEW" (leave it null for GO and NO-GO). Choose:
    - "Partially Matched" — the RFP DOES address this item, and the company's profile
      partially meets it, but not clearly enough to call it a firm GO (e.g. the RFP asks
      for a specific certification and the company has a related-but-not-identical one).
    - "Requires Clarification" — the RFP is SILENT or too vague about this item to judge
      it at all (e.g. no mention of insurance requirements anywhere in the text).
  This distinction matters: "Partially Matched" means the proposal team has something to
  work with; "Requires Clarification" means they likely need to ask the client a question
  before responding.
- Hard rule for "Payment Terms": NET30 or better -> GO. Worse than NET30 -> NO-GO.
- Hard rule for "Insurance Requirements": required coverage <= the company's
  max_insurance_available_usd -> GO. Above it -> NO-GO.
- "reason": one or two sentences grounded in the RFP text. If the RFP doesn't mention this
  item at all, say so plainly (e.g. "Not addressed in the RFP") rather than leaving it out.
- "evidence": a short direct quote or close paraphrase from the RFP. If the RFP genuinely
  doesn't address the item, set evidence to null.
- "docRef": which source document the evidence came from — the RFP text below is marked
  with "--- Document: <filename>, Page N ---" headers whenever more than one document was
  supplied (e.g. the main RFP plus Exhibit A/B/C); cite the exact filename shown in that
  header (e.g. "RFP_Exhibit_A.pdf"). Set to null if the text has no such document markers
  (a single-document RFP) or you can't tell.
- "pageRef": the page number the evidence came from, if you can tell — cite the page
  number shown in the "--- Document: ..., Page N ---" header the relevant text appeared
  under (e.g. "Page 3"). If you can't tell, set pageRef to null rather than guessing.

It is critical that your response contains all {len(cat_items)} items — a response with
fewer items is invalid."""


def _is_quota_exhausted_error(err) -> bool:
    # Both a plain 429 rate limit AND an out-of-credits condition can surface
    # as openai.RateLimitError through OpenRouter, so check the JSON body's
    # error code/type/message rather than trusting the status code alone —
    # OpenRouter's out-of-credits response usually carries "insufficient"
    # somewhere in code/type/message.
    body = getattr(err, "body", None) or {}
    err_info = body.get("error", {}) if isinstance(body, dict) else {}
    code = str(err_info.get("code") or "").lower()
    err_type = str(err_info.get("type") or "").lower()
    message = str(getattr(err, "message", "") or err_info.get("message") or "").lower()
    return (
        "insufficient_quota" in code
        or "insufficient_quota" in err_type
        or "insufficient" in message
        or "exceeded your current quota" in message
        or "out of credit" in message
    )


def _call_openai_with_retry(client, system_prompt: str, rfp_text: str, response_schema, max_output_tokens: int = 8192):
    """Generic OpenAI caller with retry/backoff, reused for the core analysis
    call, the per-department compliance calls, the outline call, and the
    response-generation call. Returns the parsed object (typed per
    response_schema, via OpenAI's structured-outputs `.parse()` helper) or,
    if structured parsing didn't populate a result, the raw response text as
    a fallback.
    Raises QuotaExhaustedError or AnalysisError."""
    backoff = INITIAL_BACKOFF_SECONDS
    last_error = None
    current_max_tokens = max_output_tokens

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.beta.chat.completions.parse(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    # 120,000 chars (~30k tokens) comfortably fits a main RFP plus several
                    # exhibit/attachment documents combined. The old 16,000-char limit was
                    # fine for a single RFP but was silently cutting off later documents
                    # (and their "--- Document: X, Page N ---" markers) once multiple files
                    # were combined, which is why docRef was never showing up for
                    # multi-document RFPs.
                    {"role": "user", "content": "RFP TEXT:\n\n" + rfp_text[:120000]},
                ],
                response_format=response_schema,
                max_tokens=current_max_tokens,
                temperature=0.1,
            )

            choice = response.choices[0]
            finish_reason = choice.finish_reason or ""

            # The model can stop early because it genuinely ran out of output
            # budget ("length") even when max_tokens looks generous — this
            # happens occasionally with schema-constrained generation on
            # certain inputs, independent of the plain 429/5xx errors handled
            # below. Retrying with a bigger budget (no backoff needed — this
            # isn't a rate limit) usually resolves it.
            if finish_reason == "length":
                last_error = AnalysisError(
                    f"Response was truncated (length) at max_tokens={current_max_tokens}."
                )
                current_max_tokens = min(current_max_tokens * 2, 16384)
                continue

            refusal = getattr(choice.message, "refusal", None)
            if refusal:
                raise AnalysisError(f"OpenAI refused the request: {refusal}")

            if getattr(choice.message, "parsed", None) is not None:
                return choice.message.parsed

            text = (choice.message.content or "").strip()
            if not text:
                raise AnalysisError(f"OpenAI returned an empty response (finish_reason={finish_reason or 'unknown'}).")
            return text

        except openai.LengthFinishReasonError as e:
            # Belt-and-suspenders: some SDK versions raise this directly
            # instead of surfacing finish_reason == "length" on the choice.
            last_error = AnalysisError(f"Response was truncated at max_tokens={current_max_tokens}.")
            current_max_tokens = min(current_max_tokens * 2, 16384)
            continue

        except openai.AuthenticationError as e:
            raise AnalysisError(
                "OpenRouter rejected the API key (401 invalid_api_key). Double check the key "
                "in your .env / sidebar is a genuine OpenRouter key (starts with 'sk-or-v1-')."
            ) from e

        except openai.RateLimitError as e:
            if _is_quota_exhausted_error(e):
                raise QuotaExhaustedError(
                    "OpenRouter reports this API key is out of credits (or over its rate "
                    "limit). Check balance/limits at openrouter.ai/credits, or try again "
                    "shortly if this is just a short-term rate limit."
                ) from e
            last_error = e
            time.sleep(backoff)
            backoff *= 2
            continue

        except (openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError) as e:
            last_error = e
            time.sleep(backoff)
            backoff *= 2
            continue

        except openai.APIStatusError as e:
            # OpenRouter returns 402 Payment Required (not a plain 429) when
            # the key is out of credits — the base openai SDK has no
            # dedicated exception subclass for 402, so it surfaces as the
            # generic APIStatusError. Treat it the same as a quota error
            # rather than falling through to the catch-all APIError below.
            if getattr(e, "status_code", None) == 402:
                raise QuotaExhaustedError(
                    "OpenRouter reports this API key is out of credits. "
                    "Add credits at openrouter.ai/credits."
                ) from e
            raise AnalysisError(f"OpenRouter/OpenAI API error: {e}") from e

        except openai.APIError as e:
            raise AnalysisError(f"OpenRouter/OpenAI API error: {e}") from e

    raise AnalysisError(
        f"OpenAI kept failing after {MAX_RETRIES} attempts (transient errors). "
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
            f"Could not parse OpenAI's {label} response as JSON: {e}. Got: \"{preview}...\""
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
        raise AnalysisError("No OpenAI API key configured.")
    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
    prompt = _build_outline_prompt(company_profile)
    result = _call_openai_with_retry(client, prompt, rfp_text, ProposalOutline, max_output_tokens=4096)
    if isinstance(result, ProposalOutline):
        outline = result.model_dump()
    else:
        outline = _parse_fallback_json(result, "proposal outline")
    return _apply_outline_numbering(outline)


def _build_response_generation_prompt(questions: list, knowledge_base: dict) -> str:
    kb_json = json.dumps(knowledge_base, indent=2)
    questions_list = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    return f"""You are a proposal writer for SPS, drafting first-pass answers to questions an RFP
has asked. Answer EVERY question using ONLY the facts in the COMPANY KNOWLEDGE BASE below —
do not invent capabilities, certifications, project history, or figures that aren't present
in it. If the knowledge base genuinely doesn't cover something a question asks for, say so
plainly in the response rather than making it up.

Also use the RFP TEXT (provided as the main content) for context — reference the RFP's own
specifics (e.g. its scope, department count, technology) where relevant, so the answer reads
as written FOR this RFP rather than as a generic boilerplate paragraph.

COMPANY KNOWLEDGE BASE (the only source of truth for company facts):
{kb_json}

QUESTIONS TO ANSWER (answer every one, in this exact order):
{questions_list}

For each question, write a professional, proposal-ready response of 2-4 sentences. Respond
with ONLY a raw JSON object (no commentary, no markdown fences) matching:
{{
  "responses": [
    {{"question": "string (repeat the question as given)", "response": "string",
      "basedOn": "string|null (which knowledge base section(s) grounded this, e.g. \\"project_portfolio, security_compliance\\", or null if the question needed no specific section)"}}
  ]
}}
"responses" must contain exactly {len(questions)} entries, in the same order as the questions
above."""


def generate_question_responses(rfp_text: str, questions: list, api_key: str) -> list:
    """
    Phase 6 (AI Response Generation): drafts a first-pass proposal answer for
    each question extracted in Phase 2 (analyze_rfp's "questions" field),
    grounded in the company knowledge base (knowledge_base.py) rather than
    the model's own guesses.

    Deliberately a SEPARATE, on-demand call (triggered by a button in the
    UI) rather than something analyze_rfp always runs automatically — not
    every RFP has extracted questions, and this is the kind of step a real
    proposal writer would trigger deliberately, not something that should
    silently cost extra API calls on every analysis.

    questions: list of question strings (or the extracted-question dicts —
    only the "question" text is used here).
    Returns a list of {"question", "response", "basedOn"} dicts.
    """
    if not api_key:
        raise AnalysisError("No OpenAI API key configured.")
    if not questions:
        return []

    question_texts = [q.get("question") if isinstance(q, dict) else str(q) for q in questions]

    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
    kb = get_full_knowledge_base()
    prompt = _build_response_generation_prompt(question_texts, kb)
    result = _call_openai_with_retry(client, prompt, rfp_text, ResponseGenerationResult, max_output_tokens=8192)

    if isinstance(result, ResponseGenerationResult):
        return result.model_dump()["responses"]
    parsed = _parse_fallback_json(result, "response generation")
    return parsed.get("responses", [])


def analyze_rfp(rfp_text: str, company_profile: dict, api_key: str, doc_names: list = None) -> dict:
    """
    Runs the full analysis as multiple OpenAI calls:
      1. Core analysis (verdict, deliverables, criteria, dates/budget, risks, strengths).
      2. The compliance checklist, split into one call PER DEPARTMENT (Financial,
         Legal, Operations, Technical) rather than one call for all 35 items.
         A single schema requiring an exact-length array of 35 complex nested
         objects pushes against structured-outputs limits and hurts reliability —
         splitting into 4 smaller exact-length arrays (6/13/11/5 items) keeps
         each call's schema small enough to be answered reliably, while still
         guaranteeing an exact item count per department.
    If any individual department call fails, the others still proceed — a
    single failed department degrades to "REVIEW — not returned" for just
    that department's items rather than failing the whole analysis.
    Results are merged back onto the fixed checklist (so the report always
    covers exactly the right items regardless of ordering) and the
    deterministic hard-rule overrides are applied on top.

    doc_names: list of source document filenames when the RFP was assembled
    from multiple files (main RFP + exhibits/attachments) — passed through to
    the prompts so the model can reliably cite which document a deliverable
    point or checklist item's evidence came from (docRef).
    """
    if not api_key:
        raise AnalysisError("No OpenAI API key configured.")

    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)

    # --- Call 1: core analysis ---
    core_prompt = _build_core_system_prompt(company_profile, doc_names)
    core_result = _call_openai_with_retry(client, core_prompt, rfp_text, RFPCoreAnalysis, max_output_tokens=16384)
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
            prompt = _build_compliance_system_prompt(company_profile, category, doc_names)
            schema = build_category_checklist_schema(cat_count)
            result = _call_openai_with_retry(client, prompt, rfp_text, schema, max_output_tokens=8192)
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
            "gapType": (found or {}).get("gapType") or ("Requires Clarification" if not found else None),
            "reason": (found or {}).get("reason", "Not returned by the model — re-run the analysis or check this item manually."),
            "evidence": (found or {}).get("evidence"),
            "docRef": (found or {}).get("docRef"),
            "pageRef": (found or {}).get("pageRef"),
        })
    return merged
