"""Deterministic pre-flight linter: catches Power-BI-open-time errors statically."""

from __future__ import annotations

from app.assembler import assemble_pbip
from app.schemas.generation import ReportArtifacts, SemanticModelArtifacts
from app.validation.preflight import lint_model, lint_report, lint_tree, preflight


def _categories(findings) -> set[str]:
    return {f.category for f in findings}


def test_lint_model_clean_passes(sample_model):
    findings = lint_model(sample_model)
    assert not [f for f in findings if f.severity == "error"], [
        f.message for f in findings
    ]


def test_lint_model_flags_space_indentation_and_yaml_bool_and_mode():
    bad_table = (
        "table Sales\n"
        "    column Amount\n"          # space indentation
        "        dataType: decimal\n"
        "    partition Sales = m\n"
        "        mode: Import\n"        # wrong casing
        "        source = let x = 1 in x\n"
    )
    model = SemanticModelArtifacts(
        model_tmdl="model M\n\tlegacyRedirects: off\n",  # YAML boolean
        tables={"Sales.tmdl": bad_table},
    )
    cats = _categories(lint_model(model))
    assert "tmdl.indentation" in cats
    assert "tmdl.boolean" in cats
    assert "tmdl.mode" in cats


def test_lint_model_flags_invalid_m_type_and_datatype():
    table = (
        "table T\n"
        "\tcolumn C\n"
        "\t\tdataType: notatype\n"          # invalid TMDL data type
        "\tpartition T = m\n"
        "\t\tmode: import\n"
        '\t\tsource = let t = Table.TransformColumnTypes(s, {{"C", type int64}}) in t\n'
    )
    model = SemanticModelArtifacts(model_tmdl="model M\n", tables={"T.tmdl": table})
    cats = _categories(lint_model(model))
    assert "tmdl.m_type" in cats
    assert "tmdl.datatype" in cats


def test_lint_model_flags_missing_table_decl_and_partition():
    model = SemanticModelArtifacts(
        model_tmdl="model M\n",
        tables={"Broken.tmdl": "\tcolumn C\n\t\tdataType: int64\n"},
    )
    findings = lint_model(model)
    cats = _categories(findings)
    assert "tmdl.structure" in cats  # missing 'table <Name>'
    assert any(f.category == "tmdl.partition" and f.severity == "warning" for f in findings)


def test_lint_model_skips_m_block_spaces(sample_model):
    """Spaces inside a multi-line M source block must NOT be flagged as bad indentation."""
    table = (
        "table T\n"
        "\tcolumn C\n"
        "\t\tdataType: int64\n"
        "\tpartition T = m\n"
        "\t\tmode: import\n"
        "\t\tsource = ```\n"
        "                let\n"            # spaces here are legitimate M
        "                    Source = 1\n"
        "                in\n"
        "                    Source\n"
        "                ```\n"
    )
    model = SemanticModelArtifacts(model_tmdl="model M\n", tables={"T.tmdl": table})
    assert "tmdl.indentation" not in _categories(lint_model(model))


def test_lint_report_flags_unknown_reference(sample_model, invalid_report):
    cats = _categories(lint_report(invalid_report, sample_model))
    assert "pbir.cross_ref" in cats


def test_lint_report_flags_missing_visual_type(sample_model):
    report = ReportArtifacts(
        report_json={},
        pages=[{
            "page_id": "P1",
            "page_json": {"name": "P1"},
            "visuals": [{"visual_id": "v1", "visual_json": {"visual": {}}}],
        }],
    )
    findings = lint_report(report, sample_model)
    assert "pbir.visual" in _categories(findings)
    assert "pbir.schema" in _categories(findings)  # missing $schema -> warning


def test_lint_report_clean_passes(sample_model, valid_report):
    # valid_report omits $schema on its visual, so expect at most schema warnings.
    errors = [f for f in lint_report(valid_report, sample_model) if f.severity == "error"]
    assert not errors, [f.message for f in errors]


def test_lint_tree_passes_on_assembled_project(tmp_path, sample_model, valid_report):
    root = assemble_pbip("MyReport", sample_model, valid_report, tmp_path)
    errors = [f for f in lint_tree(root) if f.severity == "error"]
    assert not errors, [f.message for f in errors]


def test_lint_tree_flags_missing_pbism(tmp_path, sample_model, valid_report):
    root = assemble_pbip("MyReport", sample_model, valid_report, tmp_path)
    (root / "MyReport.SemanticModel" / "definition.pbism").unlink()
    findings = lint_tree(root)
    assert any(
        f.category == "structure.missing_file" and "definition.pbism" in f.file
        for f in findings
    )


def test_preflight_combines_all_layers(tmp_path, sample_model, valid_report):
    root = assemble_pbip("MyReport", sample_model, valid_report, tmp_path)
    findings = preflight(sample_model, valid_report, root)
    # Clean artifacts -> no hard errors across model + report + tree.
    assert not [f for f in findings if f.severity == "error"], [
        f.message for f in findings
    ]
