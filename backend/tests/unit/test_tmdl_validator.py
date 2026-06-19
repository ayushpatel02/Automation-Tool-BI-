"""Structural TMDL fallback checks (TE2-independent path)."""

import pytest

from app.generation.semantic_model import (
    _normalize_m_types,
    _normalize_tmdl_booleans,
    _normalize_tmdl_indentation,
    _normalize_tmdl_mode,
)
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


def test_normalize_m_types_fixes_invalid_identifiers():
    """TMDL-style M type names must become valid Power Query M types."""
    m = (
        'let Source = Sql.Database("h", "d"), '
        'data = Source{[Item="Sales"]}[Data], '
        'typed = Table.TransformColumnTypes(data, {'
        '{"Id", type int64}, '
        '{"Name", type string}, '
        '{"Price", type decimal}, '
        '{"Ratio", type double}, '
        '{"When", type dateTime}, '
        '{"Flag", type boolean}'
        '}) in typed'
    )
    out = _normalize_m_types(m)
    assert "type int64" not in out
    assert "Int64.Type" in out
    assert "type text" in out          # string -> text
    assert "Currency.Type" in out      # decimal -> Currency.Type
    assert "type number" in out        # double -> number
    assert "type datetime" in out      # dateTime -> datetime
    assert "type logical" in out       # boolean -> logical


def test_normalize_tmdl_indentation_converts_spaces_to_tabs():
    """LLM-generated TMDL with 4-space indentation must be rewritten to tabs."""
    spaced = (
        "model M\n"
        "    culture: en-US\n"
        "    defaultPowerBIDataSourceVersion: powerBI_V3\n"
    )
    result = _normalize_tmdl_indentation(spaced)
    assert result == "model M\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"


def test_normalize_tmdl_indentation_preserves_m_source_block():
    """M source code inside backtick blocks must not have its indentation changed."""
    tmdl = (
        "table Sales\n"
        "    partition Sales = m\n"
        "        mode: import\n"
        "        source = ```\n"
        "                let\n"
        "                    Source = Sql.Database(\"s\", \"d\")\n"
        "                in\n"
        "                    Source\n"
        "                ```\n"
    )
    result = _normalize_tmdl_indentation(tmdl)
    # TMDL structural lines converted
    assert "\tpartition Sales = m\n" in result
    assert "\t\tmode: import\n" in result
    # M source lines preserved as-is
    assert "                let\n" in result
    assert "                    Source = Sql.Database" in result


def test_normalize_tmdl_indentation_already_tab_indented_unchanged():
    tab_tmdl = "model M\n\tculture: en-US\n"
    assert _normalize_tmdl_indentation(tab_tmdl) == tab_tmdl


def test_normalize_tmdl_mode_fixes_title_case():
    """mode: Import and mode: DirectQuery must become mode: import / mode: directQuery."""
    tmdl = (
        "partition P = m\n"
        "\tmode: Import\n"
        "partition Q = m\n"
        "\tmode: DirectQuery\n"
        "partition R = m\n"
        "\tmode: DualMode\n"
        "partition S = m\n"
        "\tmode: import\n"  # already correct — must not change
    )
    result = _normalize_tmdl_mode(tmdl)
    assert "\tmode: import\n" in result
    assert "\tmode: directQuery\n" in result
    assert "\tmode: dualMode\n" in result
    assert "Import" not in result
    assert "DirectQuery" not in result
    assert "DualMode" not in result


def test_normalize_m_types_leaves_tmdl_datatype_and_valid_m_untouched():
    # TMDL `dataType: int64` is NOT an M `type` expression — must be left alone.
    tmdl_col = "\tcolumn Id\n\t\tdataType: int64\n\t\tsourceColumn: Id\n"
    assert _normalize_m_types(tmdl_col) == tmdl_col
    # Already-valid M types must be preserved.
    valid_m = 'Table.TransformColumnTypes(t, {{"A", type text}, {"B", type number}})'
    assert _normalize_m_types(valid_m) == valid_m
