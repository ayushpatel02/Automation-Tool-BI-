"""Report layout preview extraction."""

from app.schemas.generation import ReportArtifacts
from app.services.preview import build_preview


def test_build_preview_extracts_visuals(valid_report):
    pv = build_preview(valid_report)

    assert len(pv["pages"]) == 1
    page = pv["pages"][0]
    assert page["width"] == 1280  # default applied when page_json omits it
    assert page["height"] == 720
    assert len(page["visuals"]) == 1

    visual = page["visuals"][0]
    assert visual["type"] == "barChart"
    assert "Sales.Total Sales" in visual["fields"]
    assert "Products.Category" in visual["fields"]


def test_build_preview_handles_empty_report():
    pv = build_preview(ReportArtifacts(report_json={}, pages=[]))
    assert pv["pages"] == []
