"""Lenient JSON parsing and model registry."""

import pytest

from app.llm import available_models
from app.llm.router import LLMError, _parse_json_lenient, model_config


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
