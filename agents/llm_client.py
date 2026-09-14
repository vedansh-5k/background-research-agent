"""
All calls to the LLM go through this one file.

Two providers are supported, switchable via LLM_PROVIDER in .env:
  - "groq"   (default) — much higher free-tier rate limits, good for active testing
  - "gemini" — kept as an alternative; its free tier is stricter (20 req/min)

Provider SDKs change their exact syntax fairly often. If something here breaks
after an upgrade, this is the ONLY file you should need to fix — nothing else
in the project talks to an LLM provider directly.
"""
import json
import re
import time

from pydantic import BaseModel

from config.settings import settings

MAX_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = [10, 25, 45]
# Longer than this and we stop waiting and switch providers instead — a daily
# quota error politely suggests retrying in ~25 minutes, which is not a wait a
# user should sit through.
MAX_RETRY_WAIT_SECONDS = 25


class LLMError(Exception):
    pass


def _is_payload_too_large(error: Exception) -> bool:
    text = str(error).lower()
    return "413" in text or "too large" in text or "reduce your message size" in text


def _is_transient_generation_failure(error: Exception) -> bool:
    """The model occasionally returns output that doesn't validate against our
    schema (more common on smaller models). A fresh attempt often succeeds since
    this is sampling variance, not a structural problem with the request."""
    text = str(error).lower()
    return "json_validate_failed" in text or "failed to validate json" in text


def _is_daily_quota(error: Exception) -> bool:
    """A per-DAY quota won't clear in any wait a user would tolerate."""
    text = str(error).lower()
    return "per day" in text or "(tpd)" in text or "tokens per day" in text


def _suggested_wait_seconds(error: Exception, fallback: float) -> float:
    text = str(error)
    # Providers phrase this as "retry in 20.4s" or "try again in 24m57.744s".
    match = re.search(r"(?:retry|try again) in (?:(\d+)m)?([\d.]+)s", text, re.IGNORECASE)
    if match:
        try:
            minutes = int(match.group(1)) if match.group(1) else 0
            return minutes * 60 + float(match.group(2)) + 1  # small buffer
        except ValueError:
            pass
    return fallback


def _classify_retry(error: Exception, attempt: int) -> tuple[bool, float]:
    """Returns (should_retry, wait_seconds)."""
    if _is_payload_too_large(error):
        # Same request fails identically every time — retrying wastes 3 rounds
        # of backoff for nothing. This needs a smaller prompt, not a wait.
        return False, 0

    if _is_daily_quota(error):
        # Nothing to wait for — switch providers instead.
        return False, 0

    if _is_transient_generation_failure(error):
        return True, 3.0

    text = str(error).lower()
    if "429" in text or "rate limit" in text or "rate_limit_exceeded" in text or "quota" in text or "resource_exhausted" in text:
        wait = _suggested_wait_seconds(error, DEFAULT_BACKOFF_SECONDS[min(attempt, len(DEFAULT_BACKOFF_SECONDS) - 1)])
        # A per-minute limit is worth waiting out; a per-DAY quota is not — that
        # error asks you to come back in ~25 minutes, and sleeping that long
        # looks identical to the app hanging. Fail fast instead so the caller
        # can fall back to the other provider immediately.
        if wait > MAX_RETRY_WAIT_SECONDS:
            return False, 0
        return True, wait

    return False, 0


def _call_with_retry(fn, logger=None):
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as e:
            last_error = e
            should_retry, wait = _classify_retry(e, attempt)
            if not should_retry or attempt == MAX_RETRIES:
                raise
            if logger:
                logger.log(
                    "LLM",
                    f"Generation issue (attempt {attempt + 1}/{MAX_RETRIES}) — retrying in {wait:.0f}s: {e}",
                )
            time.sleep(wait)
    raise last_error  # pragma: no cover — unreachable, loop always returns or raises


# ---------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------

def _groq_client():
    from groq import Groq

    if not settings.groq_api_key:
        raise LLMError("GROQ_API_KEY is not set in your .env file.")
    return Groq(api_key=settings.groq_api_key)


def _groq_structured(system_prompt: str, user_prompt: str, schema: type[BaseModel]) -> BaseModel:
    """Plain JSON mode + our own validation, deliberately NOT the provider's
    schema-constrained mode. Constrained decoding against this nested schema kept
    failing server-side with an empty generation, which threw away the entire run.
    json_object mode only guarantees syntactically valid JSON, which the model
    handles reliably; conformance is then checked here, where a near-miss can be
    retried instead of discarded by the provider."""
    client = _groq_client()
    schema_text = json.dumps(schema.model_json_schema(), separators=(",", ":"))
    completion = client.chat.completions.create(
        model=settings.groq_model,
        messages=[
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\n\nReturn ONLY a single JSON object conforming to "
                    f"this JSON Schema (no markdown, no commentary):\n{schema_text}"
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        # Without this, the model hit a low default output ceiling and got cut
        # off mid-JSON — the document then failed validation and EVERY extracted
        # fact was lost, leaving reports with empty evidence.
        max_completion_tokens=settings.max_completion_tokens,
        reasoning_effort=settings.groq_reasoning_effort,
        response_format={"type": "json_object"},
    )
    raw = completion.choices[0].message.content
    if not raw or not raw.strip():
        usage = getattr(completion, "usage", None)
        raise LLMError(
            f"Model returned an empty response (finish_reason="
            f"{completion.choices[0].finish_reason}, usage={usage}). This usually "
            f"means reasoning tokens consumed the whole output budget — raise "
            f"MAX_COMPLETION_TOKENS or lower GROQ_REASONING_EFFORT."
        )
    return schema.model_validate(json.loads(raw))


def _groq_text(system_prompt: str, user_prompt: str) -> str:
    client = _groq_client()
    completion = client.chat.completions.create(
        model=settings.groq_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return completion.choices[0].message.content


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def _gemini_client():
    from google import genai

    if not settings.gemini_api_key:
        raise LLMError("GEMINI_API_KEY is not set in your .env file.")
    return genai.Client(api_key=settings.gemini_api_key)


def _gemini_structured(system_prompt: str, user_prompt: str, schema: type[BaseModel]) -> BaseModel:
    client = _gemini_client()
    interaction = client.interactions.create(
        model=settings.gemini_model,
        system_instruction=system_prompt,
        input=user_prompt,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": schema.model_json_schema(),
        },
    )
    return schema.model_validate_json(interaction.output_text)


def _gemini_text(system_prompt: str, user_prompt: str) -> str:
    client = _gemini_client()
    interaction = client.interactions.create(
        model=settings.gemini_model,
        system_instruction=system_prompt,
        input=user_prompt,
    )
    return interaction.output_text


# ---------------------------------------------------------------------------
# Public interface — this is what the rest of the app calls
# ---------------------------------------------------------------------------

def _has_key(provider: str) -> bool:
    return bool(settings.groq_api_key if provider == "groq" else settings.gemini_api_key)


def _providers_in_order() -> list[str]:
    """Configured provider first, then the other one if it has a key.

    Free tiers run out — Groq's is capped per DAY, Gemini's per minute — and an
    exhausted quota shouldn't kill a run when a second provider is sitting
    configured and idle. Same reasoning as the search-provider fallback.
    """
    primary = settings.llm_provider if settings.llm_provider in ("groq", "gemini") else "groq"
    order = [primary] if _has_key(primary) else []
    other = "gemini" if primary == "groq" else "groq"
    if _has_key(other):
        order.append(other)
    return order or [primary]


def _run_with_fallback(kind: str, per_provider, logger=None):
    failures = []
    providers = _providers_in_order()
    for i, provider in enumerate(providers):
        try:
            return _call_with_retry(lambda p=provider: per_provider(p), logger)
        except Exception as e:
            failures.append(f"{provider}: {e}")
            is_last = i == len(providers) - 1
            if logger and not is_last:
                logger.log("LLM", f"{provider} unavailable ({str(e)[:120]}) — falling back to {providers[i + 1]}")
            if is_last:
                raise LLMError(f"{kind} failed on all providers -> " + " | ".join(failures)) from e


def generate_structured(
    system_prompt: str, user_prompt: str, schema: type[BaseModel], logger=None
) -> BaseModel:
    """Ask the configured LLM for output matching a Pydantic schema, falling back
    to the other configured provider if the first is rate-limited or exhausted."""
    return _run_with_fallback(
        "Structured generation",
        lambda p: _gemini_structured(system_prompt, user_prompt, schema)
        if p == "gemini"
        else _groq_structured(system_prompt, user_prompt, schema),
        logger,
    )


def generate_text(system_prompt: str, user_prompt: str, logger=None) -> str:
    """Ask the configured LLM for plain prose (used for narrative report sections)."""
    return _run_with_fallback(
        "Text generation",
        lambda p: _gemini_text(system_prompt, user_prompt)
        if p == "gemini"
        else _groq_text(system_prompt, user_prompt),
        logger,
    )
