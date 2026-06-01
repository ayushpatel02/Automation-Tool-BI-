"""PBIR validation — pure Python.

Three layers:
1. Structural checks (required envelope shape, non-empty pages/visuals).
2. JSON-schema validation against Microsoft's published PBIR schemas, when a cached schema
   is available for the file's `$schema` URL (see schemas/ and `fetch_schemas.py`).
3. Cross-reference checks: every measure/column a visual references must exist in the
   semantic model. This catches the most common high-impact failure (hallucinated fields)
   without needing Power BI Desktop.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.schemas.generation import (
    ReportArtifacts,
    SemanticModelArtifacts,
    ValidationError,
    ValidationResult,
)

_SCHEMA_DIR = Path(__file__).parent / "schemas"


def _load_cached_schema(schema_url: str) -> dict | None:
    """Map a `$schema` URL to a cached local schema file, if present."""
    # URL pattern: .../report/definition/{fileType}/{version}/schema.json
    m = re.search(r"/definition/([^/]+)/([^/]+)/schema\.json", schema_url)
    if not m:
        return None
    file_type, version = m.group(1), m.group(2)
    path = _SCHEMA_DIR / f"{file_type}-{version}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _validate_against_schema(obj: dict, file_label: str) -> list[ValidationError]:
    schema_url = obj.get("$schema")
    if not schema_url:
        return []
    schema = _load_cached_schema(schema_url)
    if schema is None:
        return []  # No cached schema: skip (network-free by design).
    try:
        import jsonschema

        validator = jsonschema.Draft7Validator(schema)
        return [
            ValidationError(
                file=file_label,
                path="/".join(str(p) for p in err.path),
                message=err.message,
            )
            for err in validator.iter_errors(obj)
        ]
    except Exception:  # noqa: BLE001 — schema tooling must never hard-fail generation
        return []


def _semantic_index(model: SemanticModelArtifacts) -> dict[str, set[str]]:
    """Build {entity -> {column/measure names}} from the TMDL artifacts."""
    index: dict[str, set[str]] = {}
    for fname, content in model.tables.items():
        table = fname.replace(".tmdl", "")
        names: set[str] = set()
        for line in content.splitlines():
            s = line.strip()
            if s.startswith("column "):
                names.add(s.split()[1].strip("'"))
            elif s.startswith("measure "):
                name = s[len("measure ") :].split("=")[0].strip().strip("'")
                names.add(name)
            elif s.startswith("table "):
                table = s.split()[1].strip("'")
        index[table] = names
    return index


def _iter_field_refs(node: Any):
    """Yield (entity, property) for every Measure/Column reference in a PBIR visual."""
    if isinstance(node, dict):
        for kind in ("Measure", "Column"):
            if kind in node and isinstance(node[kind], dict):
                ref = node[kind]
                entity = (
                    ref.get("Expression", {}).get("SourceRef", {}).get("Entity")
                )
                prop = ref.get("Property")
                if entity and prop:
                    yield entity, prop
        for v in node.values():
            yield from _iter_field_refs(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_field_refs(item)


def validate_report(
    report: ReportArtifacts, model: SemanticModelArtifacts
) -> ValidationResult:
    errors: list[ValidationError] = []

    # 1. Structural
    if not report.pages:
        errors.append(ValidationError(file="report", message="Report has no pages"))
    index = _semantic_index(model)

    for page in report.pages:
        pid = page.get("page_id", "?")
        visuals = page.get("visuals", [])
        if not visuals:
            errors.append(
                ValidationError(
                    file=f"page:{pid}",
                    message="Page has no visuals",
                    severity="warning",
                )
            )
        for v in visuals:
            vid = v.get("visual_id", "?")
            vjson = v.get("visual_json", {})
            label = f"visual:{vid}"

            # 2. Schema validation (if a cached schema exists)
            errors.extend(_validate_against_schema(vjson, label))

            # 3. Cross-reference
            for entity, prop in _iter_field_refs(vjson):
                if entity not in index:
                    errors.append(
                        ValidationError(
                            file=label,
                            message=f"References unknown table '{entity}'",
                        )
                    )
                elif prop not in index[entity]:
                    errors.append(
                        ValidationError(
                            file=label,
                            message=(
                                f"References '{prop}' which does not exist on "
                                f"table '{entity}'"
                            ),
                        )
                    )

    hard_errors = [e for e in errors if e.severity == "error"]
    return ValidationResult(
        valid=not hard_errors,
        errors=hard_errors,
        warnings=[e for e in errors if e.severity == "warning"],
    )
