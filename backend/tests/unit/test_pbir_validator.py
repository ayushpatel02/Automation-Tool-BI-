"""Cross-reference validation: visuals must only reference real model fields."""

from app.validation import validate_report


def test_valid_report_passes(sample_model, valid_report):
    result = validate_report(valid_report, sample_model)
    assert result.valid
    assert not result.errors


def test_unknown_measure_fails(sample_model, invalid_report):
    result = validate_report(invalid_report, sample_model)
    assert not result.valid
    assert any("Nonexistent" in e.message for e in result.errors)


def test_no_pages_is_invalid(sample_model, valid_report):
    valid_report.pages = []
    result = validate_report(valid_report, sample_model)
    assert not result.valid


def test_unknown_entity_fails(sample_model, valid_report):
    # Point a column ref at a table that does not exist.
    visual = valid_report.pages[0]["visuals"][0]["visual_json"]
    visual["visual"]["query"]["queryState"]["Category"]["projections"][0]["field"] = {
        "Column": {"Expression": {"SourceRef": {"Entity": "Ghost"}}, "Property": "X"}
    }
    result = validate_report(valid_report, sample_model)
    assert not result.valid
    assert any("Ghost" in e.message for e in result.errors)
