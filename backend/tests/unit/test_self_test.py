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
from app.schemas.generation import (
    PreflightFinding,
    ReportArtifacts,
    SemanticModelArtifacts,
)
from app.validation.self_test import (
    _infer_repair_target,
    _route_llm_findings,
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


class ReviewThenRepairLLM:
    """Flags a missing visual on the review pass, then 'adds' it on the repair pass.

    Branches on message content so a single fake can serve both the LLM self-review and the
    subsequent repair call the way the real router does.
    """

    def __init__(self, fixed_report: dict):
        self.config = {"supports_structured_output": False}
        self._fixed = fixed_report
        self.review_calls = 0
        self.repair_calls = 0
        self._repaired = False

    async def complete_json(self, messages, json_schema=None):
        text = " ".join(m.get("content", "") for m in messages)
        if "meticulous Power BI reviewer" in text:  # the self-review system prompt
            self.review_calls += 1
            if self._repaired:
                return {"issues": []}
            return {"issues": [{
                "category": "visual.missing",
                "severity": "error",
                "target": "report",
                "message": "The report is missing the requested table listing Date, Close, Volume.",
            }]}
        self.repair_calls += 1   # anything else is a repair pass
        self._repaired = True
        return self._fixed


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


def test_route_llm_findings_drops_nothing():
    """Every fix='llm' finding must land in exactly one repair bucket — never dropped.

    Regression: the self-review emits free-form categories like 'visual.missing' that
    matched neither the old 'tmdl/te2' nor 'pbir/structure/review' routing prefix, so the
    repair loop spun without ever dispatching a repair.
    """
    findings = [
        PreflightFinding(category="visual.missing", message="missing table", fix="llm",
                         layer="llm_review", repair_target="report"),
        PreflightFinding(category="measure.logic", message="YoY is a SUM", fix="llm",
                         layer="llm_review", repair_target="model"),
        PreflightFinding(category="pbir.cross_ref", message="unknown table", fix="llm",
                         layer="deterministic"),
        PreflightFinding(category="tmdl.datatype", message="bad type", fix="llm",
                         layer="deterministic"),
        PreflightFinding(category="te2.compile", message="compile failed", fix="llm",
                         layer="te2"),
    ]
    model_errs, report_errs = _route_llm_findings(findings)
    assert "missing table" in report_errs
    assert "unknown table" in report_errs
    assert "YoY is a SUM" in model_errs
    assert "bad type" in model_errs
    assert "compile failed" in model_errs
    assert len(model_errs) + len(report_errs) == len(findings)  # nothing lost


def test_infer_repair_target():
    assert _infer_repair_target("visual.missing") == "report"      # a visual → report
    assert _infer_repair_target("pbir.cross_ref") == "report"
    assert _infer_repair_target("review.issue") == "report"
    assert _infer_repair_target("measure.logic") == "model"        # a DAX measure → model
    assert _infer_repair_target("format.currency") == "model"      # formatString → model
    assert _infer_repair_target("tmdl.indentation") == "model"


@pytest.mark.asyncio
async def test_repair_loop_routes_review_missing_visual_to_report(
    tmp_path, sample_model, valid_report
):
    """A review 'visual.missing' error must reach the report repair prompt and converge."""
    fixed = {
        "report_json": {"$schema": "report"},
        "pages": [{"page_id": "P1", "page_json": {"name": "P1"}, "visuals": [{
            "visual_id": "t1",
            "visual_json": {"$schema": _VC, "name": "t1", "visual": {
                "visualType": "tableEx",
                "query": {"queryState": {"Values": {"projections": [{"field": {
                    "Column": {"Expression": {"SourceRef": {"Entity": "Sales"}},
                               "Property": "Amount"}}}]}}},
            }},
        }]}],
    }
    fake = ReviewThenRepairLLM(fixed)
    result = await self_test_and_repair(
        model=sample_model, report=valid_report, profile=_profile(), request="r",
        project_name="R", output_dir=tmp_path, llm=fake, max_attempts=3,
    )
    assert fake.repair_calls >= 1, "review 'visual.missing' finding was dropped, not repaired"
    assert result.report_card.passed, [f.message for f in result.report_card.errors]


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
