"""Classify a natural-language edit into the artifact scopes it touches.

A cheap, fast LLM call returns the set of layers/operations the edit implies, so the refiner
can regenerate only what is needed (and detect cross-layer edits that touch both TMDL and
PBIR). Falls back to FULL_REGENERATION on any uncertainty.
"""

from __future__ import annotations

import json

from app.llm.router import LLMError, LLMRouter

VALID_SCOPES = {
    "TMDL_NEW_MEASURE",
    "TMDL_MODIFY_MEASURE",
    "TMDL_STRUCTURE",
    "PBIR_VISUAL_TYPE",
    "PBIR_VISUAL_QUERY",
    "PBIR_FORMATTING",
    "PBIR_NEW_VISUAL",
    "PBIR_NEW_PAGE",
    "FULL_REGENERATION",
}

_PROMPT = """Classify this Power BI report edit request into the artifact scopes it affects.
Return ONLY JSON: {{"scope": ["..."], "target_hint": "<which visual/measure, or empty>"}}

Valid scope values:
- TMDL_NEW_MEASURE: add a new DAX measure
- TMDL_MODIFY_MEASURE: change an existing measure
- TMDL_STRUCTURE: change tables/columns/relationships
- PBIR_VISUAL_TYPE: change a visual's chart type
- PBIR_VISUAL_QUERY: change which fields a visual shows
- PBIR_FORMATTING: change colors/titles/labels
- PBIR_NEW_VISUAL: add a visual
- PBIR_NEW_PAGE: add a page
- FULL_REGENERATION: too broad for a targeted edit

Edit request: "{edit}"
"""


async def classify_edit(llm: LLMRouter, edit: str) -> dict:
    try:
        raw = await llm.complete_json(
            [{"role": "user", "content": _PROMPT.format(edit=edit)}],
            max_tokens=512,
        )
    except LLMError:
        return {"scope": ["FULL_REGENERATION"], "target_hint": ""}

    scopes = [s for s in raw.get("scope", []) if s in VALID_SCOPES]
    if not scopes:
        scopes = ["FULL_REGENERATION"]
    return {"scope": scopes, "target_hint": raw.get("target_hint", "")}


def _safe_dump(obj) -> str:
    return json.dumps(obj, indent=2)[:4000]
