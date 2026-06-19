"""Structural TMDL fallback checks (TE2-independent path)."""

import pytest

from app.generation.semantic_model import _normalize_tmdl_booleans
from app.schemas.generation import SemanticModelArtifacts
from app.validation import validate_semantic_model


@pytest.mark.asyncio
async def test_valid_model_passes_structural(sample_model):
    result = await validate_semantic_model(sample_model)
    assert result.valid


@pytest.mark.asyncio
async def test_empty_model_fails():
    model = SemanticModelArtifacts(model_tmdl="", tables={})
    result = await validate_semantic_model(model)
    assert not result.valid


@pytest.mark.asyncio
async def test_invalid_datatype_flagged():
    bad = "table T\n\tcolumn C\n\t\tdataType: notatype\n\tpartition T = m\n\t\tsource = x\n"
    model = SemanticModelArtifacts(model_tmdl="model M\n", tables={"T.tmdl": bad})
    result = await validate_semantic_model(model)
    assert not result.valid
    assert any("notatype" in e.message for e in result.errors)


def test_normalize_tmdl_booleans():
    """YAML-style on/off/yes/no must be rewritten to TMDL true/false."""
    raw = (
        "model Model\n"
        "\tculture: en-US\n"
        "\tlegacyRedirects: off\n"
        "\tdiscourageImplicitMeasures: on\n"
        "\tsomeFlag: no\n"
        "\tanotherFlag: yes\n"
        "\tisHidden: OFF\n"          # case-insensitive
        "\tisAvailable: true\n"      # already correct — must not change
        "\tisActive: false\n"        # already correct — must not change
        "\tname: 'turn off noise'\n" # 'off' inside a string value — must not change
    )
    result = _normalize_tmdl_booleans(raw)
    assert "legacyRedirects: false" in result
    assert "discourageImplicitMeasures: true" in result
    assert "someFlag: false" in result
    assert "anotherFlag: true" in result
    assert "isHidden: false" in result       # case-insensitive
    assert "isAvailable: true" in result     # unchanged
    assert "isActive: false" in result       # unchanged
    assert "'turn off noise'" in result      # string not modified
