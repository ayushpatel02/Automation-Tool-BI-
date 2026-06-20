"""Provider-agnostic LLM router built on LiteLLM.

A single interface (`complete`, `complete_json`) serves every provider. The concrete model
is chosen at call time via a LiteLLM "provider/model" id, so switching from Gemini to Claude
or GPT is a config change, not a code change. Routing through LiteLLM also insulates us from
provider API churn — e.g. Gemini's June-2026 `response_mime_type` -> `response_format`
migration is handled inside LiteLLM.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

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
        max_tokens: int = 16384,
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
        max_tokens: int = 32768,
    ) -> dict:
        """Request JSON output. Uses structured output when the schema is provided and the
        provider supports it; otherwise falls back to JSON mode + tolerant parsing.

        max_tokens defaults to 32 768 (raised from 8 192) because TMDL generation for a
        multi-table schema can easily produce 10 000+ output tokens and a truncated JSON
        has no closing brace, causing the parser to fail with "Model did not return JSON".
        """
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

        if not content.strip():
            # Empty content — most likely an invalid/missing API key or provider error.
            raise LLMError(
                "Model returned empty content. Check that your API key is set and valid "
                f"for the selected model ({self.model_id})."
            )

        return _parse_json_lenient(content, model_id=self.model_id)


# ---------------------------------------------------------------------------
# JSON extraction helpers
# ---------------------------------------------------------------------------

# Matches the opening of a fenced code block, capturing the optional language tag.
_FENCE_RE = re.compile(r"```(?:json)?\s*\n?", re.IGNORECASE)


def _parse_json_lenient(content: str, *, model_id: str = "") -> dict:
    """Parse JSON from a model response, tolerating prose, fences, and thinking tokens.

    Strategy (each step only runs if the previous failed):
      1. Direct parse — model obeyed JSON mode perfectly.
      2. Strip leading ```[json] fence and trailing ``` — common with some providers.
      3. Extract the outermost {...} block — handles prose preamble/postamble, thinking
         tokens, or a JSON block embedded in a longer response.
      4. Raise with a diagnostic excerpt so engineers can see what the model returned.
    """
    original = content  # kept for the error message
    content = content.strip()

    # --- 1. Direct parse --------------------------------------------------
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # --- 2. Strip markdown fence ------------------------------------------
    m = _FENCE_RE.search(content)
    if m:
        body = content[m.end():]
        fence_end = body.rfind("```")
        if fence_end != -1:
            body = body[:fence_end]
        try:
            result = json.loads(body.strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # --- 3. Extract outermost {...} ---------------------------------------
    # Use a brace counter to find the outermost JSON object even if there is
    # text before or after it (prose preamble, trailing notes, thinking output).
    first_brace = content.find("{")
    if first_brace != -1:
        depth = 0
        in_str = False
        escape = False
        last_close = -1
        for i, ch in enumerate(content[first_brace:], start=first_brace):
            if escape:
                escape = False
                continue
            if ch == "\\" and in_str:
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    last_close = i
                    break  # found the complete outermost object
        if last_close != -1:
            candidate = content[first_brace : last_close + 1]
            try:
                result = json.loads(candidate)
                if isinstance(result, dict):
                    logger.debug(
                        "JSON extracted from offset %d–%d (model %s had prose around JSON)",
                        first_brace, last_close, model_id,
                    )
                    return result
            except json.JSONDecodeError as exc:
                snippet = _excerpt(original)
                raise LLMError(
                    f"Model returned a JSON-like block but it is malformed: {exc}\n"
                    f"Response excerpt: {snippet}"
                ) from exc

    # --- 4. Nothing found -------------------------------------------------
    snippet = _excerpt(original)
    raise LLMError(
        "Model did not return JSON. "
        "This usually means the output was truncated (try a shorter schema), "
        "the API key is invalid, or the model ignored the JSON instruction.\n"
        f"Response excerpt ({model_id}): {snippet}"
    )


def _excerpt(text: str, max_chars: int = 300) -> str:
    """Return a safe excerpt of model output for error messages."""
    text = text.strip()
    if len(text) <= max_chars:
        return repr(text)
    return repr(text[:max_chars] + " …[truncated]")

