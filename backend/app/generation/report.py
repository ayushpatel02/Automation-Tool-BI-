"""Stage 2: generate the PBIR report from the validated semantic model + user request."""

from __future__ import annotations

import json
from pathlib import Path

from app.llm.router import LLMRouter
from app.schemas.generation import ReportArtifacts, SemanticModelArtifacts

_PROMPTS = Path(__file__).parent / "prompts"

# PBIR response envelope schema passed to the model for structured output.
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


def semantic_model_summary(model: SemanticModelArtifacts) -> str:
    """A condensed listing of tables + measures the model may reference in visuals."""
    parts: list[str] = []
    for fname, content in model.tables.items():
        table = fname.replace(".tmdl", "")
        cols = [
            line.strip().split()[1]
            for line in content.splitlines()
            if line.strip().startswith("column ")
        ]
        measures = [
            line.strip().split("=")[0].replace("measure", "").strip().strip("'")
            for line in content.splitlines()
            if line.strip().startswith("measure ")
        ]
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


def parse_artifacts(raw: dict) -> ReportArtifacts:
    return ReportArtifacts(
        report_json=raw.get("report_json", {}),
        pages=raw.get("pages", []),
    )


async def generate_report(
    llm: LLMRouter, model: SemanticModelArtifacts, user_request: str
) -> tuple[ReportArtifacts, dict, list[dict]]:
    messages = build_messages(model, user_request)
    schema = PBIR_RESPONSE_SCHEMA if llm.config.get("supports_structured_output") else None
    raw = await llm.complete_json(messages, json_schema=schema)
    return parse_artifacts(raw), raw, messages
