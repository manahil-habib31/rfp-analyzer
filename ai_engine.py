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

from checklist_items import CHECKLIST_ITEMS, CATEGORY_ORDER
from schemas import RFPAnalysis
from decision_rules import apply_hard_rules

MODEL_NAME = "gemini-2.5-flash"
MAX_RETRIES = 4
INITIAL_BACKOFF_SECONDS = 2


class QuotaExhaustedError(Exception):
    """Raised when Gemini's daily free-tier quota is used up. Not retryable."""
    pass


class AnalysisError(Exception):
    """Raised for any other unrecoverable analysis failure."""
    pass


def _build_system_prompt(company_profile: dict) -> str:
    item_list = "\n".join(
        f"{i+1}. [{it['category']}] {it['item']} — {it['question']}"
        for i, it in enumerate(CHECKLIST_ITEMS)
    )
    profile_lines = "\n".join(f"- {k}: {v}" for k, v in company_profile.items())

    return f"""You are an RFP capture assistant. You read an incoming RFP and evaluate it
against the company's standing checklist AND its specific profile/capabilities, item by item.

COMPANY PROFILE (use this to judge fit, not generic assumptions):
{profile_lines}

CHECKLIST — answer every one of these {len(CHECKLIST_ITEMS)} items, in this exact order:
{item_list}

For each checklist item, decide:
- "status": "MET" (requirement is satisfied or favorable given the company profile),
  "GAP" (requirement is not satisfied, or a hard threshold is exceeded), or
  "REVIEW" (needs a human judgment call, or the RFP doesn't provide enough detail).
- Hard rule for "Payment Terms": NET30 or better -> MET. Worse than NET30 -> GAP
  (note it should be escalated to Accounting).
- Hard rule for "Insurance Requirements": required coverage <= the company's
  max_insurance_available_usd -> MET. Above it -> GAP.
- "reason": one or two sentences grounded in the RFP text, explaining the decision.
  If the RFP doesn't mention it, say so plainly.
- "evidence": the specific supporting text from the RFP — a short direct quote or close
  paraphrase, ideally with a section/clause reference (e.g. "Section 4.2: ..."). If the
  RFP genuinely doesn't address this item, set evidence to null rather than inventing one.

Also produce:
- An overall verdict: "score" (0-100 fit score), "tag" ("GO", "CONDITIONAL", or "NO-GO"),
  and a 2-4 sentence "summary" explaining the call for a Proposal Capture Manager.
- "deliverables": each with "description", "mandatory" (true/false — false if optional/
  nice-to-have), and "effortEstimateWeeks" (your best-effort numeric estimate, or null).
- "evaluationCriteria": [{{"criterion", "weightPercent"}}], ordered by weight descending.
- "keyDatesBudget": {{"submissionDeadline", "submissionDeadlineISO" (YYYY-MM-DD or null),
  "contractValueUSD" (number or null), "paymentTermsDays" (number or null),
  "insuranceAmountUSD" (number or null), "bondRequired" (true/false/null), "bondDetails"}}.
- "risks": 3-6 entries, each {{"risk", "severity": "HIGH"|"MEDIUM"|"LOW", "note"}},
  covering the most significant reasons to hesitate on this bid (or state there are
  no significant risks if that's genuinely the case).
- "strengths": 3-6 entries, each {{"point", "note"}}, covering the most significant reasons
  TO pursue this bid — favorable terms, strong capability alignment, relationship value, etc.
  This is the positive counterpart to "risks": together they should give a Proposal Capture
  Manager a balanced strengths-vs-weaknesses view, not just a list of problems.

Respond with ONLY a raw JSON object (no commentary, no markdown fences) matching:
{{
  "verdict": {{"score": number, "tag": "GO"|"CONDITIONAL"|"NO-GO", "summary": "string"}},
  "deliverables": [{{"description": "string", "mandatory": boolean, "effortEstimateWeeks": number|null}}],
  "evaluationCriteria": [{{"criterion": "string", "weightPercent": number|null}}],
  "compliance": [{{"item": "exact item name from the checklist above", "status": "MET"|"GAP"|"REVIEW", "reason": "string", "evidence": "string|null"}}],
  "keyDatesBudget": {{"submissionDeadline": "string|null", "submissionDeadlineISO": "string|null", "contractValueUSD": number|null, "paymentTermsDays": number|null, "insuranceAmountUSD": number|null, "bondRequired": boolean|null, "bondDetails": "string|null"}},
  "risks": [{{"risk": "string", "severity": "HIGH"|"MEDIUM"|"LOW", "note": "string"}}],
  "strengths": [{{"point": "string", "note": "string"}}]
}}
"compliance" must contain exactly the same {len(CHECKLIST_ITEMS)} items, in the same order,
with the exact item names given above — do not add, remove, reorder, or rename any."""


def _is_daily_quota_error(err: errors.APIError) -> bool:
    msg = (getattr(err, "message", "") or str(err)).lower()
    return "quota" in msg and ("day" in msg or "daily" in msg or "per day" in msg)


def _call_gemini_with_retry(client, system_prompt: str, rfp_text: str):
    """Calls Gemini, retrying transient errors with exponential backoff.
    Returns the parsed RFPAnalysis object (or raw dict as a fallback).
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
                    response_schema=RFPAnalysis,
                    max_output_tokens=8192,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            # When response_schema is set, the SDK auto-parses into that type.
            if getattr(response, "parsed", None) is not None:
                return response.parsed
            # Fallback: parse the raw text ourselves if .parsed didn't populate
            # (e.g. the model's output didn't strictly validate against the
            # schema — this still gives us something to work with rather than
            # failing outright).
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
                # per-minute rate limit — worth retrying
                last_error = e
                time.sleep(backoff)
                backoff *= 2
                continue
            # other 4xx errors (bad key, bad request, permission) are not retryable
            raise AnalysisError(f"Gemini rejected the request: {e}") from e

        except errors.ServerError as e:
            # transient 500/503 — worth retrying
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


def analyze_rfp(rfp_text: str, company_profile: dict, api_key: str) -> dict:
    """
    Runs the full analysis: builds the prompt, calls Gemini with retry/backoff,
    parses the JSON, and merges the compliance results back onto the fixed
    checklist so the report always covers exactly the 34 items regardless of
    how the model orders its response.
    """
    if not api_key:
        raise AnalysisError("No Gemini API key configured.")

    client = genai.Client(api_key=api_key)
    system_prompt = _build_system_prompt(company_profile)

    result = _call_gemini_with_retry(client, system_prompt, rfp_text)

    if isinstance(result, RFPAnalysis):
        # The schema-constrained path — already validated and typed.
        data = result.model_dump()
    else:
        # Fallback path: result is a raw string (schema validation didn't
        # populate .parsed). Parse it defensively.
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            preview = cleaned[:300].replace("\n", " ")
            raise AnalysisError(
                f"Could not parse Gemini's response as JSON: {e}. Got: \"{preview}...\""
            ) from e

    data["compliance"] = _merge_compliance(data.get("compliance", []))
    data = apply_hard_rules(data, company_profile)
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
        })
    return merged
