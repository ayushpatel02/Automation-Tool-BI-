"""Build a lightweight layout preview of a generated PBIR report.

Power BI visuals cannot be rendered without Power BI Desktop, but we can extract each page's
visual layout (type, title, pixel position, and the fields it binds) so the UI can draw a
wireframe. This gives the user a sense of the report structure before opening the .pbip.
"""

from __future__ import annotations

from app.schemas.generation import ReportArtifacts
from app.validation.pbir_validator import _iter_field_refs


def _title(visual_json: dict) -> str:
    try:
        expr = (
            visual_json.get("visual", {})
            .get("title", {})
            .get("text", {})
            .get("expr", {})
            .get("Literal", {})
            .get("Value")
        )
        if expr:
            return str(expr).strip().strip("'\"")
    except Exception:  # noqa: BLE001 — title is best-effort
        pass
    return ""


def build_preview(report: ReportArtifacts) -> dict:
    """Return {pages: [{name, width, height, visuals: [{type, title, x, y, w, h, fields}]}]}."""
    pages: list[dict] = []
    for page in report.pages:
        pj = page.get("page_json", {}) or {}
        visuals: list[dict] = []
        for v in page.get("visuals", []):
            vj = v.get("visual_json", {}) or {}
            pos = vj.get("position", {}) or {}
            vis = vj.get("visual", {}) or {}
            vtype = vis.get("visualType", "visual")
            fields = sorted({f"{e}.{p}" for e, p in _iter_field_refs(vj)})
            visuals.append(
                {
                    "id": v.get("visual_id", ""),
                    "type": vtype,
                    "title": _title(vj) or vtype,
                    "x": int(pos.get("x", 0)),
                    "y": int(pos.get("y", 0)),
                    "width": int(pos.get("width", 200)),
                    "height": int(pos.get("height", 150)),
                    "fields": fields,
                }
            )
        pages.append(
            {
                "name": pj.get("displayName") or pj.get("name") or page.get("page_id", "Page"),
                "width": int(pj.get("width", 1280)),
                "height": int(pj.get("height", 720)),
                "visuals": visuals,
            }
        )
    return {"pages": pages}
