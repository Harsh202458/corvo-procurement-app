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
    name = "gemini"

    def __init__(self, api_key: str, model: str):
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self.model = model

    def complete_json(self, system, user, schema=None):
        from google.genai import types

        cfg = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            temperature=0,
            **({"response_json_schema": schema} if schema else {}),
        )
        try:
            resp = self._client.models.generate_content(model=self.model, contents=user, config=cfg)
            return json.loads(resp.text)
        except Exception as e:  # network, quota, bad JSON
            raise LLMUnavailable(str(e)) from e


def get_llm() -> LLMClient:
    if settings.llm_provider == "gemini" and settings.gemini_api_key:
        return GeminiClient(settings.gemini_api_key, settings.gemini_model)
    return NullClient()
