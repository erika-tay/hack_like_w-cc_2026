"""One door to the LLM. Everything else in the app calls generate_json / generate_text from here.

Why a wrapper instead of calling the Gemini SDK directly everywhere:
  * Provider swap. The app talks to this file, not to a vendor SDK, so changing providers is
    one small class (see GeminiProvider).
  * AI-down handling. Every failure that means "try again later" (no key, rate limit, outage,
    no network, switched off) becomes LLMUnavailable. Callers catch that one exception and fall
    back to the no-AI path instead of crashing.
  * Quota. Free tiers allow only a small number of requests per day, so identical requests are
    cached on disk. Re-running extraction, or re-asking a demo question, costs nothing.
  * Demo switch. set_enabled(False) (or LLM_PROVIDER=none) makes every call raise
    LLMUnavailable, so you can "turn the AI off" on stage and show the app degrade gracefully.
"""
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:  # python-dotenv is optional; real environment variables still work
    pass

log = logging.getLogger("llm")
CACHE_PATH = Path(os.environ.get("LLM_CACHE_DB", ROOT / "backend" / "llm_cache.db"))
MAX_RETRIES = 2  # extra attempts for temporary problems (rate limit, server error, network)


class LLMUnavailable(Exception):
    """The AI can't be reached right now: no key, quota used up, outage, or switched off.

    transient=True means a retry in a few seconds might work; False means it won't (e.g. bad key).
    """

    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient


class LLMError(Exception):
    """The AI answered but the answer was unusable, or the request itself was wrong."""


_state = {"enabled": True, "provider": None}
_stats = {"calls": 0, "cache_hits": 0, "failures": 0}


def default_model() -> str:
    # Flash-Lite models tend to have the most generous free-tier limits. Check your real limits in
    # Google AI Studio and override with GEMINI_MODEL if you have headroom.
    return os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")


def set_enabled(flag: bool) -> None:
    _state["enabled"] = flag


def is_enabled() -> bool:
    return _state["enabled"] and os.environ.get("LLM_PROVIDER", "gemini") != "none"


def set_provider(provider) -> None:
    """Swap in another provider (tests use a scripted one). None restores the default."""
    _state["provider"] = provider


def stats() -> dict:
    return {**_stats, "enabled": is_enabled()}


# ---------------------------------------------------------------- provider: Gemini

def _translate(e: Exception):
    """Map SDK and network exceptions onto our two exception types. None = not ours, re-raise."""
    import httpx
    from google.genai import errors

    if isinstance(e, errors.APIError):
        code = getattr(e, "code", None)
        if code in (401, 403):
            return LLMUnavailable("Gemini rejected the API key. Check GEMINI_API_KEY in .env.")
        if code == 429:
            return LLMUnavailable("Gemini rate limit or daily free-tier quota reached.", transient=True)
        if isinstance(code, int) and code >= 500:
            return LLMUnavailable(f"Gemini is having problems (HTTP {code}).", transient=True)
        return LLMError(f"Gemini request failed (HTTP {code}): {e}")
    if isinstance(e, (httpx.HTTPError, OSError, TimeoutError)):
        return LLMUnavailable("Could not reach Gemini (network problem).", transient=True)
    return None


class GeminiProvider:
    name = "gemini"

    def __init__(self):
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMUnavailable(
                "No GEMINI_API_KEY found. Create a free key in Google AI Studio and put it in .env."
            )
        from google import genai
        self._client = genai.Client(api_key=key)

    def generate(self, system: str, user: str, schema: dict | None, model: str) -> str:
        from google.genai import errors, types

        def call(use_schema: bool):
            instruction = system
            kwargs = {}
            if schema is not None:
                kwargs["response_mime_type"] = "application/json"
                if use_schema:
                    kwargs["response_json_schema"] = schema
                else:  # fallback: describe the shape in words instead of enforcing it
                    instruction += "\n\nReply with JSON that matches this JSON Schema:\n" + json.dumps(schema)
            config = types.GenerateContentConfig(system_instruction=instruction, **kwargs)
            return self._client.models.generate_content(model=model, contents=user, config=config)

        try:
            try:
                response = call(True)
            except errors.ClientError as e:
                # HTTP 400 can mean Gemini rejected our schema (it supports only part of JSON Schema).
                if schema is not None and getattr(e, "code", None) == 400:
                    log.warning("Gemini rejected the response schema (%s); retrying with the schema in the prompt.", e)
                    response = call(False)
                else:
                    raise
        except Exception as e:
            translated = _translate(e)
            if translated is None:
                raise
            raise translated from e

        if not response.text:
            raise LLMError("Gemini returned an empty reply (it may have been blocked by a safety filter).")
        return response.text


def _default_provider():
    if _state["provider"] is None:
        _state["provider"] = GeminiProvider()
    return _state["provider"]


# ---------------------------------------------------------------- cache

def _cache_enabled() -> bool:
    return os.environ.get("LLM_CACHE", "1") != "0"


def _cache_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(CACHE_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS llm_cache ("
                 "key TEXT PRIMARY KEY, response TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    return conn


def _cache_get(key: str) -> str | None:
    with closing(_cache_conn()) as conn:
        row = conn.execute("SELECT response FROM llm_cache WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _cache_put(key: str, response: str) -> None:
    with closing(_cache_conn()) as conn:
        conn.execute("INSERT OR REPLACE INTO llm_cache (key, response) VALUES (?, ?)", (key, response))
        conn.commit()


# ---------------------------------------------------------------- public API

def _generate(system, user, schema, model, use_cache, parse=None):
    if not is_enabled():
        raise LLMUnavailable("The AI is switched off.")
    provider = _state["provider"] or _default_provider()
    model = model or default_model()
    key = hashlib.sha256(json.dumps([provider.name, model, system, user, schema], sort_keys=True).encode()).hexdigest()

    caching = use_cache and _cache_enabled()
    if caching:
        hit = _cache_get(key)
        if hit is not None:
            _stats["cache_hits"] += 1
            return parse(hit) if parse else hit

    for attempt in range(MAX_RETRIES + 1):
        try:
            _stats["calls"] += 1
            log.info("LLM call #%d (%s)", _stats["calls"], model)
            text = provider.generate(system, user, schema, model)
            break
        except LLMUnavailable as e:
            _stats["failures"] += 1
            if not e.transient or attempt == MAX_RETRIES:
                raise
            time.sleep(2 * (attempt + 1))

    result = parse(text) if parse else text  # parse first, so a bad reply is never cached
    if caching:
        _cache_put(key, text)
    return result


def _parse_json(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise LLMError(f"The AI returned invalid JSON ({e}). Start of reply: {cleaned[:200]!r}") from e


def generate_json(system: str, user: str, schema: dict, *, model: str | None = None, use_cache: bool = True) -> dict:
    """Ask for JSON that follows `schema`. Returns the parsed object."""
    return _generate(system, user, schema, model, use_cache, parse=_parse_json)


def generate_text(system: str, user: str, *, model: str | None = None, use_cache: bool = True) -> str:
    """Ask for free text (answers, briefs)."""
    return _generate(system, user, None, model, use_cache, parse=str.strip)
