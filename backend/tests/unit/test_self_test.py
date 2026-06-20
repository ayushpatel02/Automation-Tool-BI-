"""Self-test orchestrator: layer aggregation + the auto-repair loop."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.schemas.connector import (
    ColumnProfile,
    ConnectorType,
    SchemaProfile,
    SourceInfo,
    TableProfile,
)
from app.schemas.generation import ReportArtifacts, SemanticModelArtifacts
from app.validation.self_test import (
    quick_self_test,
    run_self_test,
    self_test_and_repair,
)

_VC = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/"
    "definition/visualContainer/2.0.0/schema.json"
)


class FakeLLM:
    """Minimal LLMRouter stand-in: returns a canned JSON dict and records calls."""

    def __init__(self, response: dict):
        self.config = {"supports_structured_output": False}
        self._response = response
        self.calls: list = []

    async def complete_json(self, messages, json_schema=None):
        self.calls.append(messages)
        return self._response


def _profile() -> SchemaProfile:
    return SchemaProfile(
        source_type="csv",
        database="d",
        sources=[SourceInfo(index=0, name="s", type=ConnectorType.CSV)],
        tables=[TableProfile(name="Sales", columns=[
            ColumnProfile(name="Amount", data_type="decimal", raw_type="numeric"),
        ])],
        profiled_at=datetime.now(UTC),
    )


def test_quick_self_test_clean_passes(sample_model, valid_report):
    card = quick_self_test(sample_model, valid_report)
    assert card.passed
    assert card.layers_run == ["deterministic"]


def test_quick_self_test_flags_bad_reference(sample_model, invalid_report):
    card = quick_self_test(sample_model, invalid_report)
    assert not card.passed
    assert any(f.category == "pbir.cross_ref" for f in card.errors)


@pytest.mark.asyncio
async def test_run_self_test_adds_llm_review_layer(sample_model, valid_report):
    fake = FakeLLM({"issues": [
        {"category": "measure.logic", "severity": "warning", "message": "Consider YoY."}
    ]})
    card = await run_self_test(sample_model, valid_report, llm=fake, request="r")
    assert "deterministic" in card.layers_run
    assert "llm_review" in card.layers_run
    assert any(f.layer == "llm_review" for f in card.findings)
    assert card.passed  # review returned only a warning


@pytest.mark.asyncio
async def test_repair_loop_heals_deterministic_faults_without_llm(tmp_path):
    """Space indentation + YAML boolean must be auto-fixed and the report pass — no LLM."""
    bad_table = (
        "table Sales\n"
        "    column Amount\n"            # space indentation (auto-fixable)
        "        dataType: decimal\n"
        "        sourceColumn: Amount\n"
        "    partition Sales = m\n"
        "        mode: Import\n"          # wrong casing (auto-fixable)
        "        source = let s = 1 in s\n"
    )
    model = SemanticModelArtifacts(
        model_tmdl="model M\n    legacyRedirects: off\n",  # space + YAML bool
        tables={"Sales.tmdl": bad_table},
    )
    report = ReportArtifacts(
        report_json={"$schema": "report"},
        pages=[{"page_id": "P1", "page_json": {"name": "P1"}, "visuals": [{
            "visual_id": "v1",
            "visual_json": {"$schema": _VC, "name": "v1", "visual": {
                "visualType": "card",
                "query": {"queryState": {"Values": {"projections": [{"field": {
                    "Column": {"Expression": {"SourceRef": {"Entity": "Sales"}},
                               "Property": "Amount"}}}]}}},
            }},
        }]}],
    )
    result = await self_test_and_repair(
        model=model, report=report, profile=_profile(), request="r",
        project_name="R", output_dir=tmp_path, llm=None,
    )
    assert result.report_card.passed, [f.message for f in result.report_card.errors]
    assert result.report_card.auto_fixed  # recorded that it normalized something
    assert result.zip_path.exists()


@pytest.mark.asyncio
async def test_repair_loop_uses_llm_for_unfixable_reference(
    tmp_path, sample_model, invalid_report
):
    """A bad field reference (LLM-fixable) is repaired by re-prompting the model."""
    fixed_report = {
        "report_json": {"$schema": "report"},
        "pages": [{"page_id": "P1", "page_json": {"name": "P1"}, "visuals": [{
            "visual_id": "v1",
            "visual_json": {"$schema": _VC, "name": "v1", "visual": {
                "visualType": "card",
                "query": {"queryState": {"Values": {"projections": [{"field": {
                    "Measure": {"Expression": {"SourceRef": {"Entity": "Sales"}},
                                "Property": "Total Sales"}}}]}}},
            }},
        }]}],
    }
    fake = FakeLLM(fixed_report)
    result = await self_test_and_repair(
        model=sample_model, report=invalid_report, profile=_profile(), request="r",
        project_name="R", output_dir=tmp_path, llm=fake,
        run_llm_review=False, max_attempts=2,
    )
    assert fake.calls, "LLM repair was never invoked"
    assert result.report_card.passed, [f.message for f in result.report_card.errors]
    assert result.report_card.attempts >= 2
