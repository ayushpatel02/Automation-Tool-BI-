"""End-to-end pipeline test for the 'Global Stock Index Overview' prompt.

No live LLM key is required: this script plays the role of the model, emitting the
*raw* JSON envelope a model would return (deliberately seeded with the four classic
LLM mistakes the sanitizers exist to fix), then drives the artifacts through the real
pipeline code:

    parse_artifacts  ->  _sanitize_tmdl
                     ->  validate_semantic_model
                     ->  validate_report   (cross-reference against the model)
                     ->  assemble_pbip + zip_pbip

It asserts every stage and prints a staged PASS/FAIL report plus the assembled .pbip
tree. Run:  uv run python scripts/test_stock_index_report.py
"""

from __future__ import annotations

import asyncio
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp

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
USER_REQUEST = (
    'Build a single-page Power BI report titled "Global Stock Index Overview" from '
    "stock index trading data. Add a slicer to filter by Index, a line chart of CloseUSD "
    "over Date, three KPI cards (latest / all-time high / all-time low CloseUSD), a bar "
    "chart of average CloseUSD by Index, and a table of Index, Date, Close and Volume."
)

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
_failures = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _failures
    mark = f"{GREEN}PASS{RESET}" if condition else f"{RED}FAIL{RESET}"
    if not condition:
        _failures += 1
    line = f"  [{mark}] {label}"
    if detail:
        line += f"  {DIM}{detail}{RESET}"
    print(line)


# --------------------------------------------------------------------------- #
# 1. Schema profile — what the profiler would return for the CSV stock data.
# --------------------------------------------------------------------------- #
def build_profile() -> SchemaProfile:
    decimals = ["Open", "High", "Low", "Close", "Adj Close", "CloseUSD"]
    cols = [ColumnProfile(name="Index", data_type="string", raw_type="text",
                          sample_values=["NYA", "N225", "HSI"])]
    cols.append(ColumnProfile(name="Date", data_type="datetime", raw_type="date"))
    cols += [ColumnProfile(name=n, data_type="decimal", raw_type="numeric") for n in decimals]
    cols.append(ColumnProfile(name="Volume", data_type="integer", raw_type="bigint"))
    table = TableProfile(name="StockIndex", schema_name="public",
                         approx_row_count=112_457, columns=cols)
    return SchemaProfile(
        source_type="csv",
        database="stock_index.csv",
        sources=[SourceInfo(index=0, name="Stock Index CSV", type=ConnectorType.CSV,
                            database="stock_index.csv")],
        tables=[table],
        inferred_relationships=[],
        profiled_at=datetime.now(UTC),
    )


# --------------------------------------------------------------------------- #
# 2. Raw TMDL envelope — as a model would emit it, WITH four injected faults:
#      (a) 4-space indentation   (TMDL requires tabs)
#      (b) mode: Import          (must be lowercase 'import')
#      (c) discourageImplicitMeasures: on   (YAML boolean; must be true)
#      (d) `type int64` / `type string` in M   (invalid M type identifiers)
# --------------------------------------------------------------------------- #
def raw_model() -> dict:
    model_tmdl = (
        "model Model\n"
        "    culture: en-US\n"
        "    defaultPowerBIDataSourceVersion: powerBI_V3\n"
        "    discourageImplicitMeasures: on\n"          # fault (c)
    )
    cols = [
        ("Index", "string"), ("Date", "dateTime"), ("Open", "decimal"),
        ("High", "decimal"), ("Low", "decimal"), ("Close", "decimal"),
        ("'Adj Close'", "decimal"), ("Volume", "int64"), ("CloseUSD", "decimal"),
    ]
    lines = ["table StockIndex"]
    for name, dtype in cols:
        src = name.strip("'")
        lines += [f"    column {name}", f"        dataType: {dtype}",
                  f"        sourceColumn: {src}"]
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
    # Single-line M source with injected invalid M types (fault d).
    m_src = (
        'let Source = Csv.Document(File.Contents("C:\\data\\stock_index.csv"), '
        "[Delimiter=\",\", Encoding=65001, QuoteStyle=QuoteStyle.Csv]), "
        "promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]), "
        'typed = Table.TransformColumnTypes(promoted, '
        '{{"Volume", type int64}, {"Index", type string}}) in typed'
    )
    lines += ["    partition StockIndex = m",
              "        mode: Import",                    # fault (b)
              f"        source = {m_src}"]
    table_tmdl = "\n".join(lines) + "\n"  # 4-space indent throughout (fault a)
    return {
        "model_tmdl": model_tmdl,
        "tables": {"StockIndex.tmdl": table_tmdl},
        "relationships_tmdl": "",
        "expressions_tmdl": "",
    }


# --------------------------------------------------------------------------- #
# 3. Raw PBIR envelope — the seven requested visuals + a title textbox.
# --------------------------------------------------------------------------- #
VC_SCHEMA = ("https://developer.microsoft.com/json-schemas/fabric/item/report/"
             "definition/visualContainer/2.0.0/schema.json")


def _col(entity: str, prop: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def _measure(entity: str, prop: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def _proj(field: dict, qref: str) -> dict:
    return {"field": field, "queryRef": qref, "active": True}


def _visual(vid: str, vtype: str, pos: dict, query_state: dict | None = None,
            extra_visual: dict | None = None) -> dict:
    visual: dict = {"visualType": vtype}
    if query_state is not None:
        visual["query"] = {"queryState": query_state}
    if extra_visual:
        visual.update(extra_visual)
    return {
        "visual_id": vid,
        "visual_json": {"$schema": VC_SCHEMA, "name": vid,
                        "position": {**pos, "z": 0, "tabOrder": pos.get("x", 0)},
                        "visual": visual},
    }


def raw_report() -> dict:
    E = "StockIndex"
    visuals = [
        _visual("title", "textbox", {"x": 20, "y": 12, "width": 1240, "height": 40},
                extra_visual={"objects": {"general": [{"properties": {"text": {
                    "expr": {"Literal": {"Value": "'Global Stock Index Overview'"}}}}}]}}),
        _visual("slicer_index", "slicer", {"x": 20, "y": 64, "width": 240, "height": 372},
                {"Values": {"projections": [_proj(_col(E, "Index"), "StockIndex.Index")]}}),
        _visual("card_latest", "card", {"x": 280, "y": 64, "width": 320, "height": 92},
                {"Values": {"projections": [
                    _proj(_measure(E, "Latest CloseUSD"), "StockIndex.Latest CloseUSD")]}}),
        _visual("card_high", "card", {"x": 620, "y": 64, "width": 320, "height": 92},
                {"Values": {"projections": [
                    _proj(_measure(E, "Max CloseUSD"), "StockIndex.Max CloseUSD")]}}),
        _visual("card_low", "card", {"x": 960, "y": 64, "width": 300, "height": 92},
                {"Values": {"projections": [
                    _proj(_measure(E, "Min CloseUSD"), "StockIndex.Min CloseUSD")]}}),
        _visual("line_trend", "lineChart", {"x": 280, "y": 172, "width": 980, "height": 248},
                {"Category": {"projections": [_proj(_col(E, "Date"), "StockIndex.Date")]},
                 "Y": {"projections": [
                     _proj(_measure(E, "Total CloseUSD"), "StockIndex.Total CloseUSD")]}}),
        _visual("bar_avg", "barChart", {"x": 20, "y": 444, "width": 620, "height": 256},
                {"Category": {"projections": [_proj(_col(E, "Index"), "StockIndex.Index")]},
                 "Y": {"projections": [
                     _proj(_measure(E, "Average CloseUSD"), "StockIndex.Average CloseUSD")]}}),
        _visual("table_detail", "tableEx", {"x": 660, "y": 444, "width": 600, "height": 256},
                {"Values": {"projections": [
                    _proj(_col(E, "Index"), "StockIndex.Index"),
                    _proj(_col(E, "Date"), "StockIndex.Date"),
                    _proj(_col(E, "Close"), "StockIndex.Close"),
                    _proj(_col(E, "Volume"), "StockIndex.Volume")]}}),
    ]
    return {
        "report_json": {
            "$schema": ("https://developer.microsoft.com/json-schemas/fabric/item/report/"
                        "definition/report/4.0.0/schema.json"),
            "themeCollection": {"baseTheme": {"name": "CY24SU10"}},
        },
        "pages": [{
            "page_id": "GlobalStockIndexOverview",
            "page_json": {
                "$schema": ("https://developer.microsoft.com/json-schemas/fabric/item/"
                            "report/definition/page/2.0.0/schema.json"),
                "name": "GlobalStockIndexOverview",
                "displayName": "Global Stock Index Overview",
                "displayOption": "FitToPage", "width": 1280, "height": 720,
            },
            "visuals": visuals,
        }],
    }


# --------------------------------------------------------------------------- #
async def main() -> int:
    print(f"\n{'='*72}\n  TEST: '{PROJECT}' prompt through the real generation pipeline"
          f"\n{'='*72}")

    profile = build_profile()
    print(f"\n[1] Schema profile  {DIM}(what the profiler feeds the model){RESET}")
    check("1 table, 9 columns", len(profile.tables) == 1
          and len(profile.tables[0].columns) == 9)
    print(f"      prompt context preview:\n{DIM}"
          + "\n".join("        " + ln for ln in
                      model_gen._schema_context(profile).splitlines()[:6]) + RESET)

    # --- Stage 1: TMDL parse + sanitize ---
    print("\n[2] Stage 1 — TMDL parse + sanitize (4 injected faults must be healed)")
    model_art = model_gen.parse_artifacts(raw_model())
    tbl = model_art.tables["StockIndex.tmdl"]
    check("(a) indentation: tabs, no 4-space lines", "\n    " not in ("\n" + tbl)
          and "\tcolumn Index" in tbl, "spaces -> tabs")
    check("(b) partition mode lowercased", "mode: import" in tbl
          and "mode: Import" not in tbl, "Import -> import")
    check("(c) YAML boolean fixed", "discourageImplicitMeasures: true" in model_art.model_tmdl
          and ": on" not in model_art.model_tmdl, "on -> true")
    check("(d) invalid M types fixed", "Int64.Type" in tbl and "type text" in tbl
          and "type int64" not in tbl and "type string" not in tbl,
          "type int64->Int64.Type, type string->type text")

    # --- Stage 1: validate ---
    print("\n[3] Stage 1 — validate_semantic_model")
    mv = await validate_semantic_model(model_art)
    check("semantic model valid", mv.valid,
          "; ".join(e.message for e in mv.errors) or "no errors")

    # --- Stage 2: PBIR parse + cross-reference validate ---
    print("\n[4] Stage 2 — PBIR parse + validate_report (cross-ref vs. model)")
    report_art = report_gen.parse_artifacts(raw_report())
    n_visuals = len(report_art.pages[0]["visuals"])
    check("8 visuals (title + 7 requested)", n_visuals == 8, f"{n_visuals} visuals")
    rv = validate_report(report_art, model_art)
    check("report valid (all field refs resolve)", rv.valid,
          "; ".join(e.message for e in rv.errors) or "no errors")
    # Confirm each requested visual type is present.
    vtypes = [v["visual_json"]["visual"]["visualType"]
              for v in report_art.pages[0]["visuals"]]
    for want in ("slicer", "lineChart", "card", "barChart", "tableEx", "textbox"):
        check(f"contains {want}", want in vtypes,
              f"x{vtypes.count(want)}" if vtypes.count(want) > 1 else "")

    # --- Assemble + zip ---
    print("\n[5] Assemble .pbip + zip")
    out = Path(mkdtemp(prefix="stockreport_"))
    root = assemble_pbip(PROJECT, model_art, report_art, out)
    zip_path = zip_pbip(root, PROJECT)
    sm = root / f"{PROJECT}.SemanticModel"
    rep = root / f"{PROJECT}.Report"
    check(".pbip entry written", (out / f"{PROJECT}.pbip").exists())
    check("definition.pbism present", (sm / "definition.pbism").exists())
    check("database.tmdl present", (sm / "definition" / "database.tmdl").exists())
    check("model.tmdl present", (sm / "definition" / "model.tmdl").exists())
    check("StockIndex.tmdl present",
          (sm / "definition" / "tables" / "StockIndex.tmdl").exists())
    check("definition.pbir present", (rep / "definition.pbir").exists())
    page_dir = rep / "definition" / "pages" / "GlobalStockIndexOverview"
    check("page.json present", (page_dir / "page.json").exists())
    check("8 visual.json files written",
          len(list((page_dir / "visuals").glob("*/visual.json"))) == 8)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    check("zip flat layout (no wrapper folder)",
          any(n == f"{PROJECT}.pbip" for n in names)
          and any(n.startswith(f"{PROJECT}.SemanticModel/") for n in names)
          and not any(n.startswith(f"{PROJECT}/") for n in names),
          f"{zip_path.name}, {len(names)} entries")

    # --- Tree ---
    print(f"\n[6] Assembled .pbip tree  {DIM}{out}{RESET}")
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print(f"      {p.relative_to(out)}")

    print(f"\n{'='*72}")
    if _failures == 0:
        print(f"  {GREEN}ALL CHECKS PASSED{RESET} — the prompt produces a valid, "
              f"self-healed .pbip.")
    else:
        print(f"  {RED}{_failures} CHECK(S) FAILED{RESET}")
    print(f"{'='*72}\n")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
