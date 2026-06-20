"""Stage 2: generate the PBIR report from the validated semantic model + user request."""

from __future__ import annotations

import json
from pathlib import Path

from app.llm.router import LLMRouter
from app.schemas.generation import ReportArtifacts, SemanticModelArtifacts

_PROMPTS = Path(__file__).parent / "prompts"

_VISUAL_CONTAINER_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/"
    "definition/visualContainer/2.0.0/schema.json"
)

# PBIR response envelope schema. NOTE: this is intentionally NOT used for structured-output
# mode. `visual_json`/`page_json`/`report_json` are free-form, deeply-nested objects, and
# Gemini's structured output (responseSchema) returns free-form objects EMPTY — which made
# every generated visual come back with no visualType and no field bindings. PBIR is
# generated in plain JSON mode instead (see `generate_report`). Kept for documentation.
PBIR_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "report_json": {"type": "object"},
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "page_json": {"type": "object"},
                    "visuals": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "visual_id": {"type": "string"},
                                "visual_json": {"type": "object"},
                            },
                            "required": ["visual_id", "visual_json"],
                        },
                    },
                },
                "required": ["page_id", "page_json", "visuals"],
            },
        },
    },
    "required": ["report_json", "pages"],
}

_VISUAL_EXAMPLE = json.dumps(
    {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.0.0/schema.json",
        "name": "550e8400-e29b-41d4-a716-446655440001",
        "position": {"x": 20, "y": 60, "z": 0, "width": 580, "height": 340, "tabOrder": 1},
        "visual": {
            "visualType": "barChart",
            "query": {
                "queryState": {
                    "Category": {
                        "projections": [
                            {
                                "field": {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Entity": "Products"}},
                                        "Property": "Category",
                                    }
                                },
                                "queryRef": "Products.Category",
                                "active": True,
                            }
                        ]
                    },
                    "Y": {
                        "projections": [
                            {
                                "field": {
                                    "Measure": {
                                        "Expression": {"SourceRef": {"Entity": "Sales"}},
                                        "Property": "Total Sales",
                                    }
                                },
                                "queryRef": "Sales.Total Sales",
                                "active": True,
                            }
                        ]
                    },
                }
            },
        },
    },
    indent=2,
)


def _load(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8")


def _tmdl_identifier(rest: str) -> str:
    """Parse a TMDL name that may be single-quoted (e.g. 'Stock Data' or Sales)."""
    rest = rest.strip()
    if rest.startswith("'"):
        end = rest.find("'", 1)
        return rest[1:end] if end > 0 else rest.strip("'")
    return rest.split()[0] if rest else rest


def semantic_model_summary(model: SemanticModelArtifacts) -> str:
    """A condensed listing of tables + measures the model may reference in visuals.

    Uses content-declared names (same as the cross-reference validator) so the names
    we give the PBIR generator exactly match what validation will check against.
    """
    parts: list[str] = []
    for fname, content in model.tables.items():
        table = fname.replace(".tmdl", "")
        cols: list[str] = []
        measures: list[str] = []
        for line in content.splitlines():
            s = line.strip()
            if s.startswith("table "):
                table = _tmdl_identifier(s[len("table "):])
            elif s.startswith("column "):
                cols.append(_tmdl_identifier(s[len("column "):]))
            elif s.startswith("measure "):
                m = s[len("measure "):].split("=")[0].strip().strip("'")
                measures.append(m)
        parts.append(f"TABLE {table}")
        if cols:
            parts.append("  columns: " + ", ".join(cols))
        if measures:
            parts.append("  measures: " + ", ".join(measures))
    return "\n".join(parts)


def build_messages(model: SemanticModelArtifacts, user_request: str) -> list[dict]:
    system = (
        _load("pbir_system.txt")
        .replace("{semantic_model_summary}", semantic_model_summary(model))
        .replace("{visual_example}", _VISUAL_EXAMPLE)
    )
    user = f"REPORT REQUEST:\n{user_request}\n\nGenerate the PBIR report now."
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_repair_messages(
    base_messages: list[dict], current: dict, errors: list[str]
) -> list[dict]:
    repair = _load("pbir_repair.txt").format(
        error_list="\n".join(f"- {e}" for e in errors),
        current_content=json.dumps(current, indent=2),
    )
    return base_messages + [{"role": "user", "content": repair}]


def _normalize_visual(vjson: dict) -> dict:
    """Repair common shape variations so a visual carries `visual.visualType` + a query.

    Even in JSON mode an LLM sometimes places ``visualType`` or ``query``/``queryState`` at
    the top level of the visual instead of inside the nested ``visual`` object, or omits the
    ``$schema``. Power BI Desktop silently drops a visual with no ``visual.visualType``, so we
    move these into place and inject a default ``$schema`` rather than ship a broken visual.
    """
    if not isinstance(vjson, dict):
        return vjson

    visual = vjson.get("visual")
    if not isinstance(visual, dict):
        visual = {}

    # visualType sometimes lands at the top level instead of under `visual`.
    if not visual.get("visualType"):
        for key in ("visualType", "type"):
            if vjson.get(key):
                visual["visualType"] = vjson.pop(key)
                break

    # query / queryState sometimes land at the top level too.
    if "query" not in visual:
        if isinstance(vjson.get("query"), dict):
            visual["query"] = vjson.pop("query")
        elif isinstance(vjson.get("queryState"), dict):
            visual["query"] = {"queryState": vjson.pop("queryState")}

    if visual:
        vjson["visual"] = visual
    vjson.setdefault("$schema", _VISUAL_CONTAINER_SCHEMA)
    return vjson


def parse_artifacts(raw: dict) -> ReportArtifacts:
    pages = raw.get("pages", []) or []
    for page in pages:
        if not isinstance(page, dict):
            continue
        for v in page.get("visuals", []) or []:
            if isinstance(v, dict) and isinstance(v.get("visual_json"), dict):
                v["visual_json"] = _normalize_visual(v["visual_json"])
    return ReportArtifacts(
        report_json=raw.get("report_json", {}),
        pages=pages,
    )


async def generate_report(
    llm: LLMRouter, model: SemanticModelArtifacts, user_request: str
) -> tuple[ReportArtifacts, dict, list[dict]]:
    messages = build_messages(model, user_request)
    # PBIR is generated in JSON mode (NOT structured output): visual_json is free-form and
    # Gemini's responseSchema returns free-form objects empty. The prompt + example carry the
    # shape, and parse_artifacts() repairs common deviations.
    raw = await llm.complete_json(messages)
    return parse_artifacts(raw), raw, messages
