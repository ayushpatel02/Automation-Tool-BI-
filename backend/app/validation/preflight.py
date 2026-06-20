"""Deterministic pre-flight linter — emulates Power BI Desktop's open-time validation.

Power BI Desktop only surfaces format errors when it actually opens a project, so the
weak structural fallback in ``tmdl_validator`` lets whole classes of failures through
(space indentation, ``mode: Import``, YAML booleans, invalid M type identifiers, a
missing ``definition.pbism``/``database.tmdl``, a broken dataset reference, hallucinated
field references, ...). This module statically checks for every one of those — no .NET,
no network, no Power BI — so the pipeline can catch and repair them before the user ever
downloads the ``.pbip``.

Each issue is a :class:`PreflightFinding` tagged with how it can be fixed (``auto`` via a
deterministic normalizer, ``llm`` via a repair prompt, or ``manual``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.schemas.generation import (
    PreflightFinding,
    ReportArtifacts,
    SemanticModelArtifacts,
)
from app.validation.pbir_validator import validate_report

_VALID_DATA_TYPES = {"string", "int64", "double", "decimal", "dateTime", "boolean", "binary"}
_VALID_MODES = {"import", "directQuery", "dualMode", "push", "streaming"}

_YAML_BOOL_RE = re.compile(r"^\s*[\w][\w.]*\s*:\s*(off|on|no|yes)\s*$", re.IGNORECASE)
_MODE_RE = re.compile(r"^\s*mode\s*:\s*(\S+)\s*$")
_DSV_RE = re.compile(r"^\s*defaultPowerBIDataSourceVersion\s*:\s*(\S+)\s*$")
_M_TYPE_RE = re.compile(
    r"\btype\s+(string|int64|double|decimal|dateTime|boolean|binary)\b", re.IGNORECASE
)
_DATATYPE_RE = re.compile(r"dataType:\s*(\S+)")
_TABLE_DECL_RE = re.compile(r"^\s*table\s+\S+", re.MULTILINE)
_M_FENCE_RE = re.compile(r"source\s*=\s*```")

# Expected Microsoft $schema URL fragments per boilerplate file (guards the format bugs
# already seen in the wild — a wrong $schema makes Power BI reject the project outright).
_SCHEMA_EXPECTATIONS = {
    "definition.pbism": "fabric/item/semanticModel/definitionProperties/",
    "definition.pbir": "fabric/item/report/definitionProperties/",
}


def _m_block_lines(content: str) -> list[bool]:
    """Return a per-line mask: True where the line is inside a Power Query (M) block.

    M source (inside ``source = ``` ... ``` ``) legitimately uses spaces, so indentation
    and identifier checks must skip it.
    """
    mask: list[bool] = []
    in_m = False
    for line in content.split("\n"):
        if not in_m and _M_FENCE_RE.search(line):
            mask.append(False)  # the `source = ```` line itself is a TMDL line
            in_m = True
            continue
        mask.append(in_m)
        if in_m and line.strip() == "```":
            in_m = False
    return mask


def _lint_tmdl_text(label: str, content: str, *, is_table: bool) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    lines = content.split("\n")
    mask = _m_block_lines(content)

    def add(category: str, message: str, *, severity: str = "error", fix: str = "manual") -> None:
        findings.append(
            PreflightFinding(
                category=category, message=message, severity=severity, file=label, fix=fix
            )
        )

    # 1. Indentation must be tabs, not spaces (outside M blocks).
    space_lines = [
        i + 1
        for i, line in enumerate(lines)
        if not mask[i] and line[:1] == " "
    ]
    if space_lines:
        preview = ", ".join(str(n) for n in space_lines[:5])
        add(
            "tmdl.indentation",
            f"{len(space_lines)} line(s) use space indentation instead of tabs "
            f"(line {preview}). TMDL requires tab indentation.",
            fix="auto",
        )

    # 2. YAML-style booleans (on/off/yes/no) instead of true/false.
    for i, line in enumerate(lines):
        if mask[i]:
            continue
        if _YAML_BOOL_RE.match(line):
            add(
                "tmdl.boolean",
                f"Line {i + 1}: boolean property uses a YAML value "
                f"({line.strip()!r}); TMDL requires true/false.",
                fix="auto",
            )

    # 3. Partition mode casing.
    for i, line in enumerate(lines):
        if mask[i]:
            continue
        m = _MODE_RE.match(line)
        if m and m.group(1) not in _VALID_MODES:
            add(
                "tmdl.mode",
                f"Line {i + 1}: partition mode {m.group(1)!r} has wrong casing; "
                f"expected one of {sorted(_VALID_MODES)}.",
                fix="auto",
            )

    # 3b. defaultPowerBIDataSourceVersion must be a TMDL enum (powerBI_V3), not a number.
    for i, line in enumerate(lines):
        if mask[i]:
            continue
        m = _DSV_RE.match(line)
        if m and not m.group(1).startswith("powerBI_V"):
            add(
                "tmdl.datasource_version",
                f"Line {i + 1}: defaultPowerBIDataSourceVersion {m.group(1)!r} is invalid; "
                f"TMDL expects an enum such as powerBI_V3, not a number.",
                fix="auto",
            )

    # 4. Invalid M type identifiers (inside M expressions).
    for i, line in enumerate(lines):
        m = _M_TYPE_RE.search(line)
        if m:
            add(
                "tmdl.m_type",
                f"Line {i + 1}: {m.group(0)!r} is not valid Power Query M; use an M type "
                f"like Int64.Type or `type text`.",
                fix="auto",
            )

    # 5. Invalid TMDL data types.
    for m in _DATATYPE_RE.finditer(content):
        if m.group(1) not in _VALID_DATA_TYPES:
            add(
                "tmdl.datatype",
                f"Invalid dataType {m.group(1)!r}; valid types are {sorted(_VALID_DATA_TYPES)}.",
                fix="llm",
            )

    # 6. TMDL comment lines (// ...).
    # TMDL has no comment syntax: a line starting with // causes Power BI Desktop to
    # reject the file with "Unexpected line type: Other!". The sanitizer strips them
    # automatically; flag them here so the finding appears in the self-test report.
    comment_lines = [
        i + 1
        for i, (line, in_m) in enumerate(zip(lines, mask, strict=False))
        if not in_m and line.strip().startswith("//")
    ]
    if comment_lines:
        preview = ", ".join(str(n) for n in comment_lines[:5])
        add(
            "tmdl.comment",
            f"{len(comment_lines)} line(s) use '//' which is not valid TMDL syntax "
            f"(line {preview}). Comments must be removed.",
            fix="auto",
        )

    # 7. Unbalanced M source fences.
    if content.count("```") % 2 != 0:
        add(
            "tmdl.m_fence",
            "Unbalanced ``` fences around a Power Query (M) source block.",
            fix="llm",
        )

    # 8. Table files must declare `table <Name>` and carry a partition.
    if is_table:
        if not _TABLE_DECL_RE.search(content):
            add("tmdl.structure", "Missing 'table <Name>' declaration.", fix="llm")
        if "partition" not in content:
            add(
                "tmdl.partition",
                "No partition/source found; the table will not load any data.",
                severity="warning",
                fix="llm",
            )
    return findings


def lint_model(model: SemanticModelArtifacts) -> list[PreflightFinding]:
    """Static TMDL checks across model.tmdl, every table, relationships and expressions."""
    findings: list[PreflightFinding] = []
    if not model.model_tmdl.strip():
        findings.append(
            PreflightFinding(
                category="tmdl.structure",
                message="model.tmdl is empty.",
                file="model.tmdl",
                fix="llm",
            )
        )
    else:
        findings += _lint_tmdl_text("model.tmdl", model.model_tmdl, is_table=False)

    if not model.tables:
        findings.append(
            PreflightFinding(
                category="tmdl.structure",
                message="No table definitions were produced.",
                file="model",
                fix="llm",
            )
        )
    for fname, content in model.tables.items():
        findings += _lint_tmdl_text(fname, content, is_table=True)

    if model.relationships_tmdl.strip():
        findings += _lint_tmdl_text(
            "relationships.tmdl", model.relationships_tmdl, is_table=False
        )
    if model.expressions_tmdl.strip():
        findings += _lint_tmdl_text(
            "expressions.tmdl", model.expressions_tmdl, is_table=False
        )
    return findings


def lint_report(
    report: ReportArtifacts, model: SemanticModelArtifacts
) -> list[PreflightFinding]:
    """PBIR checks: cross-references resolve, pages/visuals present, visual shape sane."""
    findings: list[PreflightFinding] = []

    # Reuse the existing cross-reference + structural validator and map to findings.
    result = validate_report(report, model)
    for err in result.errors:
        category = (
            "pbir.cross_ref"
            if "unknown" in err.message or "does not exist" in err.message
            else "pbir.structure"
        )
        findings.append(
            PreflightFinding(
                category=category, message=err.message, file=err.file, fix="llm"
            )
        )
    for warn in result.warnings:
        findings.append(
            PreflightFinding(
                category="pbir.structure",
                message=warn.message,
                severity="warning",
                file=warn.file,
                fix="llm",
            )
        )

    # Per-visual shape sanity (missing visualType / $schema make Desktop drop the visual).
    for page in report.pages:
        pid = page.get("page_id", "?")
        for v in page.get("visuals", []):
            vjson = v.get("visual_json", {}) or {}
            label = f"visual:{v.get('visual_id', '?')} (page {pid})"
            if not vjson.get("visual", {}).get("visualType"):
                findings.append(
                    PreflightFinding(
                        category="pbir.visual",
                        message="Visual is missing 'visual.visualType'.",
                        file=label,
                        fix="llm",
                    )
                )
            if not vjson.get("$schema"):
                findings.append(
                    PreflightFinding(
                        category="pbir.schema",
                        message="Visual is missing its '$schema' URL.",
                        severity="warning",
                        file=label,
                        fix="auto",
                    )
                )
    return findings


def lint_tree(project_root: Path) -> list[PreflightFinding]:
    """Check the assembled .pbip tree for the required files Power BI needs to open it.

    Guards the assembler-level bugs already hit in the field: a missing
    ``definition.pbism``/``database.tmdl``, a wrong ``$schema``, and a ``definition.pbir``
    whose dataset reference does not resolve to the semantic-model folder.
    """
    findings: list[PreflightFinding] = []

    def missing(file_label: str, msg: str, fix: str = "manual") -> None:
        findings.append(
            PreflightFinding(
                category="structure.missing_file", message=msg, file=file_label, fix=fix
            )
        )

    sm_dirs = list(project_root.glob("*.SemanticModel"))
    rep_dirs = list(project_root.glob("*.Report"))
    if not sm_dirs:
        missing("SemanticModel", "No .SemanticModel folder in the project.")
    if not rep_dirs:
        missing("Report", "No .Report folder in the project.")

    for sm in sm_dirs:
        if not (sm / "definition.pbism").exists():
            missing(f"{sm.name}/definition.pbism", "Missing semantic-model entry point.")
        else:
            findings += _check_schema(sm / "definition.pbism")
        if not (sm / ".platform").exists():
            missing(f"{sm.name}/.platform", "Missing .platform metadata file.")
        defn = sm / "definition"
        if not (defn / "database.tmdl").exists():
            missing(f"{sm.name}/definition/database.tmdl", "Missing database.tmdl.")
        model_tmdl = defn / "model.tmdl"
        if not model_tmdl.exists() or not model_tmdl.read_text(encoding="utf-8").strip():
            missing(f"{sm.name}/definition/model.tmdl", "Missing or empty model.tmdl.")
        if not list((defn / "tables").glob("*.tmdl")):
            missing(f"{sm.name}/definition/tables", "No table .tmdl files.")

    for rep in rep_dirs:
        pbir = rep / "definition.pbir"
        if not pbir.exists():
            missing(f"{rep.name}/definition.pbir", "Missing report entry point.")
        else:
            findings += _check_schema(pbir)
            findings += _check_dataset_reference(pbir, project_root)
        if not (rep / ".platform").exists():
            missing(f"{rep.name}/.platform", "Missing .platform metadata file.")
        if not (rep / "definition" / "report.json").exists():
            missing(f"{rep.name}/definition/report.json", "Missing report.json.")
        page_jsons = list((rep / "definition" / "pages").glob("*/page.json"))
        if not page_jsons:
            missing(f"{rep.name}/definition/pages", "No page.json files (report has no pages).")

    # The .pbip entry file sits alongside the project folder.
    if not list(project_root.parent.glob("*.pbip")):
        missing("*.pbip", "Missing .pbip project entry file.")
    return findings


def _check_schema(path: Path) -> list[PreflightFinding]:
    expected = _SCHEMA_EXPECTATIONS.get(path.name)
    if not expected:
        return []
    try:
        schema = json.loads(path.read_text(encoding="utf-8")).get("$schema", "")
    except (json.JSONDecodeError, OSError):
        return [
            PreflightFinding(
                category="structure.schema",
                message=f"{path.name} is not valid JSON.",
                file=path.name,
            )
        ]
    if expected not in schema:
        return [
            PreflightFinding(
                category="structure.schema",
                message=f"{path.name} has an unexpected $schema ({schema!r}); "
                f"Power BI may reject the project.",
                file=path.name,
            )
        ]
    return []


def _check_dataset_reference(pbir: Path, project_root: Path) -> list[PreflightFinding]:
    try:
        ref = (
            json.loads(pbir.read_text(encoding="utf-8"))
            .get("datasetReference", {})
            .get("byPath", {})
            .get("path")
        )
    except (json.JSONDecodeError, OSError):
        return []
    if not ref:
        return []
    target = (pbir.parent / ref).resolve()
    if not target.exists():
        return [
            PreflightFinding(
                category="structure.dataset_ref",
                message=f"definition.pbir points at {ref!r}, which does not resolve to "
                f"the semantic-model folder.",
                file=pbir.name,
            )
        ]
    return []


def preflight(
    model: SemanticModelArtifacts,
    report: ReportArtifacts,
    project_root: Path | None = None,
) -> list[PreflightFinding]:
    """Run every deterministic check and return the combined finding list."""
    findings = lint_model(model) + lint_report(report, model)
    if project_root is not None:
        findings += lint_tree(project_root)
    return findings
