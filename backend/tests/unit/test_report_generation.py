"""PBIR generation: JSON-mode call + visual_json normalization.

Regression guard for the bug where the recommended model (Gemini, with
`supports_structured_output: true`) produced visuals with an empty `visual_json` —
no `visual.visualType`, no field bindings — because the PBIR envelope schema declared
`visual_json` as a free-form `{"type": "object"}` and Gemini's structured output returns
free-form objects empty. The fix generates PBIR in JSON mode and normalizes the result.
"""

from __future__ import annotations

import pytest

from app.generation import report as report_gen
from app.schemas.generation import SemanticModelArtifacts

_VC = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/"
    "definition/visualContainer/2.0.0/schema.json"
)


class RecordingLLM:
    """Records the kwargs passed to complete_json and returns a canned dict."""

    def __init__(self, response: dict, *, supports_structured_output: bool = True):
        self.config = {"supports_structured_output": supports_structured_output}
        self._response = response
        self.last_kwargs: dict | None = None

    async def complete_json(self, messages, *, json_schema=None, **kwargs):
        self.last_kwargs = {"json_schema": json_schema, **kwargs}
        return self._response


def _model() -> SemanticModelArtifacts:
    table = (
        "table Sales\n"
        "\tcolumn Region\n\t\tdataType: string\n\t\tsourceColumn: Region\n"
        "\tmeasure 'Total Sales' = SUM(Sales[Amount])\n"
    )
    return SemanticModelArtifacts(model_tmdl="model M\n", tables={"Sales.tmdl": table})


def _well_formed_raw() -> dict:
    return {
        "report_json": {"$schema": "report"},
        "pages": [{
            "page_id": "P1",
            "page_json": {"name": "P1"},
            "visuals": [{
                "visual_id": "v1",
                "visual_json": {
                    "$schema": _VC,
                    "name": "v1",
                    "visual": {
                        "visualType": "barChart",
                        "query": {"queryState": {"Y": {"projections": [{"field": {
                            "Measure": {"Expression": {"SourceRef": {"Entity": "Sales"}},
                                        "Property": "Total Sales"}}}]}}},
                    },
                },
            }],
        }],
    }


@pytest.mark.asyncio
async def test_generate_report_uses_json_mode_not_structured_output():
    """The core bug fix: PBIR must NOT pass a json_schema even when the model supports it."""
    llm = RecordingLLM(_well_formed_raw(), supports_structured_output=True)
    await report_gen.generate_report(llm, _model(), "a sales report")
    assert llm.last_kwargs is not None
    assert llm.last_kwargs["json_schema"] is None, (
        "PBIR generation must use JSON mode — structured output returns visual_json empty"
    )


def test_normalize_moves_top_level_visual_type_under_visual():
    vjson = {"name": "v1", "visualType": "card", "query": {"queryState": {}}}
    out = report_gen._normalize_visual(vjson)
    assert out["visual"]["visualType"] == "card"
    assert "visualType" not in out  # moved, not duplicated
    assert out["visual"]["query"] == {"queryState": {}}
    assert "query" not in out
    assert out["$schema"] == _VC  # injected


def test_normalize_lifts_top_level_query_state():
    vjson = {"visual": {"visualType": "lineChart"}, "queryState": {"Y": {"projections": []}}}
    out = report_gen._normalize_visual(vjson)
    assert out["visual"]["query"]["queryState"] == {"Y": {"projections": []}}
    assert "queryState" not in out


def test_normalize_leaves_well_formed_visual_intact():
    good = {"$schema": _VC, "name": "v1",
            "visual": {"visualType": "barChart", "query": {"queryState": {"Y": {}}}}}
    out = report_gen._normalize_visual({**good, "visual": dict(good["visual"])})
    assert out["visual"]["visualType"] == "barChart"
    assert out["visual"]["query"] == {"queryState": {"Y": {}}}


def test_parse_artifacts_normalizes_every_visual():
    raw = {
        "report_json": {},
        "pages": [{
            "page_id": "P1", "page_json": {"name": "P1"},
            "visuals": [
                {"visual_id": "a", "visual_json": {"visualType": "card"}},
                {"visual_id": "b", "visual_json": {"visual": {"visualType": "slicer"}}},
            ],
        }],
    }
    report = report_gen.parse_artifacts(raw)
    visuals = report.pages[0]["visuals"]
    assert visuals[0]["visual_json"]["visual"]["visualType"] == "card"
    assert visuals[1]["visual_json"]["visual"]["visualType"] == "slicer"
    for v in visuals:
        assert v["visual_json"]["$schema"] == _VC
