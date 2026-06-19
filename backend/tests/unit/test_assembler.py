"""Assembler produces the expected .pbip tree."""

from app.assembler import assemble_pbip, zip_pbip


def test_assemble_creates_full_tree(tmp_path, sample_model, valid_report):
    root = assemble_pbip("MyReport", sample_model, valid_report, tmp_path)

    assert (tmp_path / "MyReport.pbip").exists()
    assert (root / "MyReport.SemanticModel" / "definition" / "model.tmdl").exists()
    assert (
        root / "MyReport.SemanticModel" / "definition" / "tables" / "Sales.tmdl"
    ).exists()
    assert (root / "MyReport.Report" / "definition.pbir").exists()
    assert (root / "MyReport.Report" / "definition" / "report.json").exists()

    page_dir = root / "MyReport.Report" / "definition" / "pages" / "ReportSection1"
    assert (page_dir / "page.json").exists()
    assert (page_dir / "visuals" / "v1" / "visual.json").exists()


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
