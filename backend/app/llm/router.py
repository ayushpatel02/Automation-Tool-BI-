"""Provider-agnostic LLM router built on LiteLLM.

A single interface (`complete`, `complete_json`) serves every provider. The concrete model
is chosen at call time via a LiteLLM "provider/model" id, so switching from Gemini to Claude
or GPT is a config change, not a code change. Routing through LiteLLM also insulates us from
provider API churn — e.g. Gemini's June-2026 `response_mime_type` -> `response_format`
migration is handled inside LiteLLM.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_MODELS_PATH = Path(__file__).parent / "models.yaml"


@lru_cache
def _load_models() -> list[dict[str, Any]]:
    with open(_MODELS_PATH) as f:
        return yaml.safe_load(f)["models"]


def available_models() -> list[dict[str, Any]]:
    """Return the public model registry (for the frontend selector)."""
    return [
        {k: m[k] for k in ("id", "display_name", "provider", "recommended")
         if k in m}
        for m in _load_models()
    ]


def model_config(model_id: str) -> dict[str, Any]:
    for m in _load_models():
        if m["id"] == model_id:
            return m
    raise ValueError(f"Unknown model_id: {model_id}")


class LLMError(RuntimeError):
    """Raised when an LLM call fails or returns unparseable output."""


class LLMRouter:
    def __init__(self, model_id: str, api_key: str | None) -> None:
        self.model_id = model_id
        self.api_key = api_key
        self.config = model_config(model_id)

    async def complete(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> str:
        import litellm

        try:
            resp = await litellm.acompletion(
                model=self.model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=self.api_key,
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"LLM completion failed: {exc}") from exc

    async def complete_json(
        self,
        messages: list[dict],
        *,
        json_schema: dict | None = None,
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> dict:
        """Request JSON output. Uses structured output when the schema is provided and the
        provider supports it; otherwise falls back to JSON mode + tolerant parsing."""
        import litellm

        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "api_key": self.api_key,
        }
        if json_schema and self.config.get("supports_structured_output"):
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "artifact", "schema": json_schema, "strict": False},
            }
        else:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = await litellm.acompletion(**kwargs)
            content = resp.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"LLM JSON completion failed: {exc}") from exc

        return _parse_json_lenient(content)


def _parse_json_lenient(content: str) -> dict:
    """Parse JSON, tolerating ```json fences and surrounding prose."""
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip().rstrip("`").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError as outer:
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except json.JSONDecodeError as exc:
                raise LLMError(f"Could not parse JSON from model output: {exc}") from exc
        raise LLMError("Model did not return JSON") from outer
