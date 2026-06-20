"""Golden end-to-end scenario: the 'Global Stock Index Overview' prompt.

Drives a realistic raw model envelope (seeded with the four classic LLM faults the
sanitizers heal) through the real pipeline — parse/sanitize -> validate model ->
validate report (cross-ref) -> assemble -> zip — and asserts the result is a valid,
flat .pbip with every requested visual. This is a regression guard for the TMDL/M
normalizers and the assembler layout.
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime

import pytest

from app.assembler import assemble_pbip, zip_pbip
from app.generation import report as report_gen
from app.generation import semantic_model as model_gen
from app.schemas.connector import (
    ColumnProfile,
    ConnectorType,
    SchemaProfile,
    SourceInfo,
    TableProfile,
)
from app.validation import validate_report, validate_semantic_model

PROJECT = "GlobalStockIndexOverview"
ENTITY = "StockIndex"
VC_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/"
    "definition/visualContainer/2.0.0/schema.json"
)


def _profile() -> SchemaProfile:
    decimals = ["Open", "High", "Low", "Close", "Adj Close", "CloseUSD"]
    cols = [ColumnProfile(name="Index", data_type="string", raw_type="text")]
    cols.append(ColumnProfile(name="Date", data_type="datetime", raw_type="date"))
    cols += [ColumnProfile(name=n, data_type="decimal", raw_type="numeric") for n in decimals]
    cols.append(ColumnProfile(name="Volume", data_type="integer", raw_type="bigint"))
    return SchemaProfile(
        source_type="csv",
        database="stock_index.csv",
        sources=[SourceInfo(index=0, name="Stock Index CSV", type=ConnectorType.CSV)],
        tables=[TableProfile(name=ENTITY, approx_row_count=112_457, columns=cols)],
        profiled_at=datetime.now(UTC),
    )


def _raw_model() -> dict:
    # NOTE: 4-space indent, `mode: Import`, `: on`, and `type int64/string` are the
    # injected faults; parse_artifacts() must heal all four.
    model_tmdl = (
        "model Model\n"
        "    culture: en-US\n"
        "    defaultPowerBIDataSourceVersion: powerBI_V3\n"
        "    discourageImplicitMeasures: on\n"
    )
    cols = [
        ("Index", "string"), ("Date", "dateTime"), ("Open", "decimal"),
        ("High", "decimal"), ("Low", "decimal"), ("Close", "decimal"),
        ("'Adj Close'", "decimal"), ("Volume", "int64"), ("CloseUSD", "decimal"),
    ]
    lines = ["table StockIndex"]
    for name, dtype in cols:
        lines += [f"    column {name}", f"        dataType: {dtype}",
                  f"        sourceColumn: {name.strip(chr(39))}"]
    measures = [
        ("Total CloseUSD", "SUM(StockIndex[CloseUSD])"),
        ("Average CloseUSD", "AVERAGE(StockIndex[CloseUSD])"),
        ("Max CloseUSD", "MAX(StockIndex[CloseUSD])"),
        ("Min CloseUSD", "MIN(StockIndex[CloseUSD])"),
        ("Latest CloseUSD",
         "CALCULATE(SUM(StockIndex[CloseUSD]), LASTNONBLANK(StockIndex[Date], 1))"),
    ]
    for mname, dax in measures:
        lines += [f"    measure '{mname}' = {dax}", '        formatString: "$#,##0.00"']
    m_src = (
        'let Source = Csv.Document(File.Contents("data.csv"), '
        "[Delimiter=\",\", Encoding=65001, QuoteStyle=QuoteStyle.Csv]), "
        "promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]), "
        'typed = Table.TransformColumnTypes(promoted, '
        '{{"Volume", type int64}, {"Index", type string}}) in typed'
    )
    lines += ["    partition StockIndex = m", "        mode: Import",
              f"        source = {m_src}"]
    return {
        "model_tmdl": model_tmdl,
        "tables": {"StockIndex.tmdl": "\n".join(lines) + "\n"},
        "relationships_tmdl": "",
        "expressions_tmdl": "",
    }


def _col(prop: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": ENTITY}}, "Property": prop}}


def _measure(prop: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": ENTITY}}, "Property": prop}}


def _vis(vid: str, vtype: str, query_state: dict | None = None) -> dict:
    visual: dict = {"visualType": vtype}
    if query_state is not None:
        visual["query"] = {"queryState": query_state}
    return {"visual_id": vid,
            "visual_json": {"$schema": VC_SCHEMA, "name": vid, "visual": visual}}


def _proj(field: dict) -> dict:
    return {"projections": [{"field": field, "active": True}]}


def _raw_report() -> dict:
    visuals = [
        _vis("title", "textbox"),
        _vis("slicer_index", "slicer", {"Values": _proj(_col("Index"))}),
        _vis("card_latest", "card", {"Values": _proj(_measure("Latest CloseUSD"))}),
        _vis("card_high", "card", {"Values": _proj(_measure("Max CloseUSD"))}),
        _vis("card_low", "card", {"Values": _proj(_measure("Min CloseUSD"))}),
        _vis("line_trend", "lineChart",
             {"Category": _proj(_col("Date")), "Y": _proj(_measure("Total CloseUSD"))}),
        _vis("bar_avg", "barChart",
             {"Category": _proj(_col("Index")), "Y": _proj(_measure("Average CloseUSD"))}),
        _vis("table_detail", "tableEx", {"Values": {"projections": [
            {"field": _col("Index")}, {"field": _col("Date")},
            {"field": _col("Close")}, {"field": _col("Volume")}]}}),
    ]
    return {
        "report_json": {"$schema": "report"},
        "pages": [{"page_id": PROJECT,
                   "page_json": {"name": PROJECT, "displayName": "Global Stock Index Overview"},
                   "visuals": visuals}],
    }


def test_stock_index_tmdl_faults_are_healed():
    model = model_gen.parse_artifacts(_raw_model())
    tbl = model.tables["StockIndex.tmdl"]
    # (a) indentation -> tabs
    assert "\tcolumn Index" in tbl
    assert "\n    " not in "\n" + tbl
    # (b) mode -> import
    assert "mode: import" in tbl and "mode: Import" not in tbl
    # (c) YAML boolean -> true
    assert "discourageImplicitMeasures: true" in model.model_tmdl
    # (d) invalid M types -> valid M
    assert "Int64.Type" in tbl and "type text" in tbl
    assert "type int64" not in tbl and "type string" not in tbl


@pytest.mark.asyncio
async def test_stock_index_model_validates():
    model = model_gen.parse_artifacts(_raw_model())
    result = await validate_semantic_model(model)
    assert result.valid, [e.message for e in result.errors]


def test_stock_index_report_cross_references_resolve():
    model = model_gen.parse_artifacts(_raw_model())
    report = report_gen.parse_artifacts(_raw_report())
    result = validate_report(report, model)
    assert result.valid, [e.message for e in result.errors]
    vtypes = [v["visual_json"]["visual"]["visualType"] for v in report.pages[0]["visuals"]]
    assert {"slicer", "lineChart", "card", "barChart", "tableEx"} <= set(vtypes)
    assert vtypes.count("card") == 3  # latest / high / low KPI cards


def test_stock_index_assembles_to_flat_pbip(tmp_path):
    model = model_gen.parse_artifacts(_raw_model())
    report = report_gen.parse_artifacts(_raw_report())
    root = assemble_pbip(PROJECT, model, report, tmp_path)
    zip_path = zip_pbip(root, PROJECT)

    sm = root / f"{PROJECT}.SemanticModel"
    assert (sm / "definition.pbism").exists()
    assert (sm / "definition" / "database.tmdl").exists()
    assert (sm / "definition" / "tables" / "StockIndex.tmdl").exists()
    page_dir = root / f"{PROJECT}.Report" / "definition" / "pages" / PROJECT
    assert len(list((page_dir / "visuals").glob("*/visual.json"))) == 8

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert any(n == f"{PROJECT}.pbip" for n in names)
    assert any(n.startswith(f"{PROJECT}.SemanticModel/") for n in names)
    assert not any(n.startswith(f"{PROJECT}/") for n in names)
