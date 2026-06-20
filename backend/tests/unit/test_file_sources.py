"""CSV/Excel file-source patching: embed file bytes so the .pbip needs no external path.

Regression coverage for "The supplied file path must be a valid absolute path":
File.Contents() requires an absolute path, so the generated .pbip must never ship a
bare relative File.Contents("name.csv") — the file is embedded as Base64 instead.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.generation.m_sources import m_source_hint
from app.generation.semantic_model import patch_file_sources
from app.schemas.connector import (
    ColumnProfile,
    ConnectorType,
    SchemaProfile,
    SourceInfo,
    TableProfile,
)
from app.schemas.generation import SemanticModelArtifacts


def _profile(tmp_file, *, original_name: str | None = None) -> SchemaProfile:
    extra = {"file_path": str(tmp_file)}
    if original_name:
        extra["original_name"] = original_name
    return SchemaProfile(
        source_type="csv",
        database=original_name or tmp_file.name,
        sources=[SourceInfo(index=0, name="data", type=ConnectorType.CSV, extra=extra)],
        tables=[TableProfile(name="StockData", columns=[
            ColumnProfile(name="id", data_type="integer", raw_type="int64"),
        ])],
        profiled_at=datetime.now(UTC),
    )


def _model(m_ref: str) -> SemanticModelArtifacts:
    table = (
        "table StockData\n"
        "\tcolumn id\n"
        "\t\tdataType: int64\n"
        "\tpartition StockData = m\n"
        "\t\tmode: import\n"
        f"\t\tsource = let Source = Csv.Document(File.Contents(\"{m_ref}\"), "
        "[Delimiter=\",\"]), promoted = Table.PromoteHeaders(Source) in promoted\n"
    )
    return SemanticModelArtifacts(model_tmdl="model M\n", tables={"StockData.tmdl": table})


def _write_csv(path, n_rows: int = 3) -> None:
    rows = "\n".join(f"{i},Row{i},{i * 10}" for i in range(n_rows))
    path.write_text(f"id,name,amount\n{rows}\n", encoding="utf-8")


def test_m_hint_uses_display_name_not_server_path(tmp_path):
    server = tmp_path / "abc123def456.csv"
    _write_csv(server)
    src = SourceInfo(
        index=0, name="data", type=ConnectorType.CSV,
        extra={"file_path": str(server), "original_name": "StockData.csv"},
    )
    hint = m_source_hint(ConnectorType.CSV, src)
    assert "StockData.csv" in hint
    assert str(server) not in hint  # the server path must never leak into the M template


def test_small_file_is_embedded_as_base64(tmp_path):
    server = tmp_path / "uuid.csv"
    _write_csv(server)
    profile = _profile(server, original_name="StockData.csv")
    model = _model("StockData.csv")

    patched, data_files = patch_file_sources(model, profile)
    out = patched.tables["StockData.tmdl"]

    assert "Binary.FromText(" in out
    assert "BinaryEncoding.Base64" in out
    assert "File.Contents(" not in out  # no relative path left behind
    assert data_files == {}


def test_no_relative_file_contents_survives_regardless_of_ref(tmp_path):
    """Single source ⇒ any File.Contents arg (even a mismatched one) gets embedded."""
    server = tmp_path / "uuid.csv"
    _write_csv(server)
    profile = _profile(server, original_name="StockData.csv")

    # LLM wrote a name that matches NOTHING in the file map (no extension, wrong name).
    for weird_ref in ("StockData", "stock_data.csv", "wrong.csv", ""):
        patched, _ = patch_file_sources(_model(weird_ref), profile)
        out = patched.tables["StockData.tmdl"]
        assert "Binary.FromText(" in out, weird_ref
        assert "File.Contents(" not in out, weird_ref


def test_embedding_is_idempotent(tmp_path):
    server = tmp_path / "uuid.csv"
    _write_csv(server)
    profile = _profile(server, original_name="StockData.csv")

    once, _ = patch_file_sources(_model("StockData.csv"), profile)
    twice, _ = patch_file_sources(once, profile)
    assert once.tables == twice.tables  # second pass is a no-op


def test_missing_original_name_falls_back_to_basename(tmp_path):
    server = tmp_path / "uuid.csv"
    _write_csv(server)
    profile = _profile(server)  # no original_name (older stored credential)
    patched, _ = patch_file_sources(_model("uuid.csv"), profile)
    assert "Binary.FromText(" in patched.tables["StockData.tmdl"]


def test_large_file_bundled_with_absolute_placeholder(tmp_path, monkeypatch):
    import app.generation.semantic_model as sm

    server = tmp_path / "uuid.csv"
    _write_csv(server)
    monkeypatch.setattr(sm, "_EMBED_LIMIT_BYTES", 0)  # force the "too large" branch

    profile = _profile(server, original_name="StockData.csv")
    patched, data_files = patch_file_sources(_model("StockData.csv"), profile)
    out = patched.tables["StockData.tmdl"]

    # File bundled for the zip, and the path left in M is ABSOLUTE (Windows-style),
    # so Power BI does not reject it as relative.
    assert data_files == {"StockData.csv": str(server)}
    assert "Binary.FromText(" not in out
    assert 'File.Contents("C:\\PowerBI-Data\\StockData.csv")' in out


def test_no_file_sources_is_a_noop():
    profile = SchemaProfile(
        source_type="postgresql", database="db",
        sources=[SourceInfo(index=0, name="pg", type=ConnectorType.POSTGRESQL)],
        tables=[], profiled_at=datetime.now(UTC),
    )
    model = SemanticModelArtifacts(model_tmdl="model M\n", tables={"T.tmdl": "table T\n"})
    patched, data_files = patch_file_sources(model, profile)
    assert patched.tables == model.tables
    assert data_files == {}


@pytest.mark.parametrize("ctype,fn", [(ConnectorType.CSV, "Csv.Document"), (ConnectorType.EXCEL, "Excel.Workbook")])
def test_m_hint_filenames_for_both_file_types(tmp_path, ctype, fn):
    src = SourceInfo(
        index=0, name="data", type=ctype,
        extra={"file_path": str(tmp_path / "x.bin"), "original_name": "Report.xlsx"},
    )
    hint = m_source_hint(ctype, src)
    assert fn in hint
    assert "Report.xlsx" in hint
