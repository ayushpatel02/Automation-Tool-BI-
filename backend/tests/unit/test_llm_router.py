"""Lenient JSON parsing, model registry, and transient-error retry."""

import sys
import types

import pytest

from app.llm import available_models
from app.llm.router import (
    LLMError,
    LLMRouter,
    _is_transient,
    _parse_json_lenient,
    model_config,
)


def test_parse_plain_json():
    assert _parse_json_lenient('{"a": 1}') == {"a": 1}


def test_parse_fenced_json():
    assert _parse_json_lenient('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_with_prose():
    assert _parse_json_lenient('Here you go:\n{"a": 1}\nDone.') == {"a": 1}


def test_parse_invalid_raises():
    with pytest.raises(LLMError):
        _parse_json_lenient("not json at all")


def test_model_registry_has_recommended():
    models = available_models()
    assert any(m.get("recommended") for m in models)


def test_model_config_lookup():
    cfg = model_config("gemini/gemini-2.5-flash")
    assert cfg["provider"] == "google"


# --- transient-error classification + retry --------------------------------

def test_is_transient_classification():
    assert _is_transient(Exception("503 UNAVAILABLE: high demand"))
    assert _is_transient(Exception("429 rate limit exceeded"))
    assert _is_transient(Exception("Read timed out"))
    assert not _is_transient(Exception("400 invalid request: bad schema"))
    assert not _is_transient(Exception("AuthenticationError: invalid api key"))


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResp:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


def _install_fake_litellm(monkeypatch, behaviors):
    """Install a fake `litellm` module whose acompletion yields each behavior in turn.

    Each behavior is either an Exception to raise or a string to return as content.
    """
    calls = {"n": 0}

    async def fake_acompletion(**kwargs):
        i = calls["n"]
        calls["n"] += 1
        b = behaviors[min(i, len(behaviors) - 1)]
        if isinstance(b, Exception):
            raise b
        return _FakeResp(b)

    fake = types.ModuleType("litellm")
    fake.acompletion = fake_acompletion
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return calls


@pytest.mark.asyncio
async def test_retry_recovers_from_transient_503(monkeypatch):
    """A 503 that clears on the 3rd try must succeed, not fail the generation."""
    import app.llm.router as router

    calls = _install_fake_litellm(monkeypatch, [
        Exception("503 UNAVAILABLE: high demand"),
        Exception("503 UNAVAILABLE: high demand"),
        '{"ok": true}',
    ])
    # Don't actually sleep between retries.
    async def _no_sleep(_):
        return None
    monkeypatch.setattr(router.asyncio, "sleep", _no_sleep)

    llm = LLMRouter("gemini/gemini-2.5-flash", api_key="k")
    result = await llm.complete_json([{"role": "user", "content": "hi"}])
    assert result == {"ok": True}
    assert calls["n"] == 3  # failed twice, succeeded on the third


@pytest.mark.asyncio
async def test_non_transient_error_is_not_retried(monkeypatch):
    """A 400 (bad request) must raise immediately without wasting retries."""
    import app.llm.router as router

    calls = _install_fake_litellm(monkeypatch, [
        Exception("400 invalid request: bad schema"),
        '{"ok": true}',  # would succeed, but we must never get here
    ])
    async def _no_sleep(_):
        return None
    monkeypatch.setattr(router.asyncio, "sleep", _no_sleep)

    llm = LLMRouter("gemini/gemini-2.5-flash", api_key="k")
    with pytest.raises(LLMError):
        await llm.complete_json([{"role": "user", "content": "hi"}])
    assert calls["n"] == 1  # no retry


@pytest.mark.asyncio
async def test_persistent_transient_error_raises_friendly_message(monkeypatch):
    """If it stays down through every retry, the final error tells the user what to do."""
    import app.llm.router as router

    _install_fake_litellm(monkeypatch, [Exception("503 UNAVAILABLE: high demand")])
    async def _no_sleep(_):
        return None
    monkeypatch.setattr(router.asyncio, "sleep", _no_sleep)

    llm = LLMRouter("gemini/gemini-2.5-flash", api_key="k")
    with pytest.raises(LLMError) as ei:
        await llm.complete_json([{"role": "user", "content": "hi"}])
    assert "temporarily unavailable" in str(ei.value).lower()
