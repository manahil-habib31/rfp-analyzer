"""
backend/llm/gemini_client.py

Centralizes every Gemini call made by the agent graph, so no agent talks
to the `google-genai` SDK directly. Retry/backoff/quota-detection logic is
carried over as-is from the existing ai_engine.py (same rules, same
exceptions) so Phase 1 behaves identically to the current single-call
version — only the caller changed, not the reliability characteristics.
"""

import time
from typing import Optional

from google import genai
from google.genai import types, errors

MODEL_NAME = "gemini-2.5-flash"
MAX_RETRIES = 4
INITIAL_BACKOFF_SECONDS = 2


class QuotaExhaustedError(Exception):
    """Raised when Gemini's daily free-tier quota is used up. Not retryable."""
    pass


class AnalysisError(Exception):
    """Raised for any other unrecoverable Gemini failure."""
    pass


def _is_daily_quota_error(err: "errors.APIError") -> bool:
    full_text = f"{getattr(err, 'message', '')} {getattr(err, 'details', '')}".lower()
    return "quota" in full_text and ("perday" in full_text or "per day" in full_text or "daily" in full_text)


class GeminiClient:
    """
    Thin, stateful wrapper around a single google-genai Client. Every agent
    receives (or constructs) one of these instead of calling the SDK
    directly — this is the ONLY place API keys, retries, and JSON parsing
    are handled.

    Usage:
        client = GeminiClient(api_key)
        result = client.generate(
            system_prompt=prompt,
            content=rfp_text,
            response_schema=SomePydanticModel,   # or None for free-text
        )
    """

    def __init__(self, api_key: str, model_name: str = MODEL_NAME):
        if not api_key:
            raise AnalysisError("No Gemini API key configured.")
        self.api_key = api_key
        self.model_name = model_name
        self._client = genai.Client(api_key=api_key)

    def generate(
        self,
        system_prompt: str,
        content: str,
        response_schema=None,
        max_output_tokens: int = 8192,
        temperature: float = 0.1,
        content_char_limit: int = 120000,
    ):
        """
        Runs one structured-JSON generation with retry/backoff.

        Returns:
            - an instance of `response_schema` if the SDK's structured
              output parsed cleanly (`response.parsed`), OR
            - the raw text response (caller is responsible for parsing)
              as a fallback.

        Raises QuotaExhaustedError or AnalysisError.
        """
        backoff = INITIAL_BACKOFF_SECONDS
        last_error = None
        current_max_tokens = max_output_tokens

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                config_kwargs = dict(
                    system_instruction=system_prompt,
                    max_output_tokens=current_max_tokens,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                    temperature=temperature,
                )
                if response_schema is not None:
                    config_kwargs["response_mime_type"] = "application/json"
                    config_kwargs["response_schema"] = response_schema

                response = self._client.models.generate_content(
                    model=self.model_name,
                    contents=[{"role": "user", "parts": [{"text": content[:content_char_limit]}]}],
                    config=types.GenerateContentConfig(**config_kwargs),
                )

                finish_reason = ""
                candidates = getattr(response, "candidates", None) or []
                if candidates:
                    finish_reason = str(getattr(candidates[0], "finish_reason", "") or "")

                if getattr(response, "parsed", None) is not None:
                    return response.parsed

                if "MAX_TOKENS" in finish_reason:
                    last_error = AnalysisError(
                        f"Response was truncated (MAX_TOKENS) at max_output_tokens={current_max_tokens}."
                    )
                    current_max_tokens = min(current_max_tokens * 2, 32768)
                    continue

                text = (response.text or "").strip()
                if not text:
                    raise AnalysisError(
                        f"Gemini returned an empty response (finish_reason={finish_reason or 'unknown'})."
                    )
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
            f"Gemini kept failing after {MAX_RETRIES} attempts (transient errors). Last error: {last_error}"
        )


_default_client: Optional[GeminiClient] = None


def get_client(api_key: str) -> GeminiClient:
    """Process-wide singleton so agents don't each spin up a fresh
    genai.Client — reuses connections across a single workflow run."""
    global _default_client
    if _default_client is None or _default_client.api_key != api_key:
        _default_client = GeminiClient(api_key)
    return _default_client
