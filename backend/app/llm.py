"""Provider-agnostic LLM client.

The LLM is only used to extract structure (chat slots, policy patches, contract
fields). It never decides a route: the deterministic engine does.
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from .config import settings


class LLMUnavailable(RuntimeError):
    pass


class LLMClient(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: dict[str, Any] | None = None) -> Any: ...


class NullClient:
    name = "none"

    def complete_json(self, system, user, schema=None):
        raise LLMUnavailable("No LLM configured; use the rule-based fallback.")


class GeminiClient:
    """Gemini with retries and fallback models: a busy model (503) or quota hit (429) moves on to the
    next model instead of failing the request."""

    name = "gemini"

    def __init__(self, api_key: str, model: str, fallbacks: list[str] | None = None, timeout_s: float = 60):
        from google import genai
        from google.genai import types

        try:  # one attempt per call: our own loop decides when to move to the next model
            opts = types.HttpOptions(timeout=int(timeout_s * 1000), retry_options=types.HttpRetryOptions(attempts=1))
        except Exception:
            opts = types.HttpOptions(timeout=int(timeout_s * 1000))
        self._client = genai.Client(api_key=api_key, http_options=opts)
        self.models = [model] + [m for m in (fallbacks or []) if m and m != model]
        self.model = model
        self.last_model: str | None = None

    def complete_json(self, system, user, schema=None):
        import time

        from google.genai import types

        cfg = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0,
            **({"response_json_schema": schema} if schema else {}),
        )
        errors = []
        for model in self.models:
            for attempt in range(2):
                try:
                    resp = self._client.models.generate_content(model=model, contents=user, config=cfg)
                    self.last_model = model
                    return json.loads(resp.text)
                except json.JSONDecodeError as e:
                    errors.append(f"{model}: reply was not JSON")
                    break
                except Exception as e:  # network, 503 busy, 429 quota, 404 retired model
                    msg = str(e)
                    errors.append(f"{model}: {msg[:120]}")
                    if any(code in msg for code in ("503", "UNAVAILABLE", "500", "INTERNAL")) and attempt == 0:
                        time.sleep(2)
                        continue
                    break
        raise LLMUnavailable("; ".join(errors))


def get_fast_llm() -> LLMClient:
    """For chat turns: quick models first and a short timeout; the regex extractor covers failures."""
    if settings.llm_provider == "gemini" and settings.gemini_api_key:
        return GeminiClient(settings.gemini_api_key, "gemini-3.1-flash-lite",
                            ["gemini-3.5-flash-lite", settings.gemini_model], timeout_s=12)
    return NullClient()


def get_llm() -> LLMClient:
    if settings.llm_provider == "gemini" and settings.gemini_api_key:
        fallbacks = [m.strip() for m in settings.gemini_fallback_models.split(",")]
        return GeminiClient(settings.gemini_api_key, settings.gemini_model, fallbacks)
    return NullClient()
