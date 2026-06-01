"""Structural TMDL fallback checks (TE2-independent path)."""

import pytest

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
