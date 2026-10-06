"""Assembler produces the expected .pbip tree."""

import json
import re

from app.assembler import assemble_pbip, zip_pbip


def test_assemble_creates_full_tree(tmp_path, sample_model, valid_report):
    root = assemble_pbip("MyReport", sample_model, valid_report, tmp_path)

    assert (tmp_path / "MyReport.pbip").exists()
    sm = root / "MyReport.SemanticModel"
    assert (sm / "definition.pbism").exists()
    assert (sm / "definition" / "database.tmdl").exists()
    assert (sm / "definition" / "model.tmdl").exists()
    assert (sm / "definition" / "tables" / "Sales.tmdl").exists()
    assert (root / "MyReport.Report" / "definition.pbir").exists()
    assert (root / "MyReport.Report" / "definition" / "report.json").exists()

    page_dir = root / "MyReport.Report" / "definition" / "pages" / "ReportSection1"
    assert (page_dir / "page.json").exists()
    assert (page_dir / "visuals" / "v1" / "visual.json").exists()


def test_pbism_schema_matches_power_bi_pattern(tmp_path, sample_model, valid_report):
    root = assemble_pbip("MyReport", sample_model, valid_report, tmp_path)
    pbism = json.loads(
        (root / "MyReport.SemanticModel" / "definition.pbism").read_text()
    )
    # Power BI Desktop validates $schema against this exact pattern.
    pattern = (
        r"^https://developer\.microsoft\.com/json-schemas/fabric/item/"
        r"semanticModel/definitionProperties/1\.[0-9]+\.[0-9]+/schema\.json$"
    )
    assert re.match(pattern, pbism["$schema"])
    # version 4.0+ signals a TMDL (definition/ folder) model, not legacy TMSL.
    assert float(pbism["version"]) >= 4.0


def test_database_tmdl_quotes_names_with_spaces(tmp_path, sample_model, valid_report):
    root = assemble_pbip("Test Report", sample_model, valid_report, tmp_path)
    db = (root / "Test Report.SemanticModel" / "definition" / "database.tmdl").read_text()
    assert "database 'Test Report'" in db
    assert "compatibilityLevel:" in db


def test_zip_pbip_creates_archive(tmp_path, sample_model, valid_report):
    import zipfile

    root = assemble_pbip("MyReport", sample_model, valid_report, tmp_path)
    zip_path = zip_pbip(root, "MyReport")
    assert zip_path.exists()
    assert zip_path.suffix == ".zip"

    # Verify flat layout: .pbip, .Report/, .SemanticModel/ must all be at the zip
    # root with NO intermediate "MyReport/" wrapping folder — this is what Power BI
    # Desktop expects (siblings, not nested).
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert any(n == "MyReport.pbip" for n in names), ".pbip missing from zip root"
    assert any(n.startswith("MyReport.Report/") for n in names), ".Report/ missing at zip root"
    assert any(n.startswith("MyReport.SemanticModel/") for n in names), ".SemanticModel/ missing"
    assert not any(n.startswith("MyReport/") for n in names), "intermediate MyReport/ folder present"


def test_platform_files_present(tmp_path, sample_model, valid_report):
    root = assemble_pbip("R", sample_model, valid_report, tmp_path)
    assert (root / "R.SemanticModel" / ".platform").exists()
    assert (root / "R.Report" / ".platform").exists()


def test_pbir_required_files_and_schemas(tmp_path, sample_model, valid_report):
    """Regression: a report that opens but renders NO visuals.

    Power BI Desktop needs version.json present and valid $schema URLs on report.json,
    pages.json, page.json and visual.json; page.json also needs displayOption. Missing
    any of these makes it open the report yet drop every visual (blank canvas).
    """
    defn = assemble_pbip("R", sample_model, valid_report, tmp_path) / "R.Report" / "definition"

    # version.json — REQUIRED, was missing entirely before.
    version = json.loads((defn / "version.json").read_text())
    assert version["version"]  # e.g. "2.0.0"
    assert "versionMetadata" in version["$schema"]

    # Every structural file carries a $schema (page.json/report.json previously did not).
    report_json = json.loads((defn / "report.json").read_text())
    assert "/report/" in report_json["$schema"]
    pages_json = json.loads((defn / "pages" / "pages.json").read_text())
    assert "/pagesMetadata/" in pages_json["$schema"]

    page_json = json.loads((defn / "pages" / "ReportSection1" / "page.json").read_text())
    assert "/page/" in page_json["$schema"]
    assert page_json["displayOption"] == "FitToPage"   # required; must be a STRING
    assert page_json["name"] == "ReportSection1"        # must equal the folder name

    visual_json = json.loads(
        (defn / "pages" / "ReportSection1" / "visuals" / "v1" / "visual.json").read_text()
    )
    assert "/visualContainer/" in visual_json["$schema"]
    assert visual_json["name"] == "v1"                  # must equal the visual folder name


def test_visual_and_page_names_forced_to_match_folder(tmp_path, sample_model):
    """If the model emits a name different from the id, the folder name wins (they must match)."""
    from app.schemas.generation import ReportArtifacts

    report = ReportArtifacts(
        report_json={},
        pages=[{
            "page_id": "pageA",
            "page_json": {"name": "WRONG_PAGE_NAME", "displayName": "A"},  # mismatched
            "visuals": [{
                "visual_id": "visX",
                "visual_json": {"name": "WRONG_VISUAL_NAME", "visual": {
                    "visualType": "card",
                    "query": {"queryState": {"Values": {"projections": [{"field": {
                        "Measure": {"Expression": {"SourceRef": {"Entity": "Sales"}},
                                    "Property": "Total Sales"}}}]}}},
                }},
            }],
        }],
    )
    defn = assemble_pbip("R", sample_model, report, tmp_path) / "R.Report" / "definition"
    page_json = json.loads((defn / "pages" / "pageA" / "page.json").read_text())
    visual_json = json.loads(
        (defn / "pages" / "pageA" / "visuals" / "visX" / "visual.json").read_text()
    )
    assert page_json["name"] == "pageA"     # folder name wins, not "WRONG_PAGE_NAME"
    assert visual_json["name"] == "visX"    # folder name wins, not "WRONG_VISUAL_NAME"
