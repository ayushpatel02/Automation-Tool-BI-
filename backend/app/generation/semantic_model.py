"""Stage 1: generate the TMDL semantic model from the schema profile + user request."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.generation.m_sources import m_source_hint
from app.llm.router import LLMRouter
from app.schemas.connector import ConnectorType, SchemaProfile, SourceInfo
from app.schemas.generation import SemanticModelArtifacts

_PROMPTS = Path(__file__).parent / "prompts"


# TMDL uses true/false for booleans, never YAML-style on/off/yes/no. Some LLMs emit the
# latter, which causes "Failed to convert value 'off' to Boolean" on PBI Desktop load.
_TMDL_YAML_BOOLEANS = re.compile(
    r"^(\s*\w[\w.]*\s*:\s*)(off|on|no|yes)(\s*)$",
    re.MULTILINE | re.IGNORECASE,
)
_BOOL_MAP = {"on": "true", "yes": "true", "off": "false", "no": "false"}


def _normalize_tmdl_booleans(tmdl: str) -> str:
    """Replace YAML-style boolean values with TMDL-required true/false."""
    return _TMDL_YAML_BOOLEANS.sub(
        lambda m: m.group(1) + _BOOL_MAP[m.group(2).lower()] + m.group(3), tmdl
    )


# Inside partition M (Power Query), `type <name>` requires a valid M type. LLMs often leak
# TMDL data-type names (int64, string, dateTime, ...) into Table.TransformColumnTypes steps,
# producing "The type identifier is invalid" on load. Map them to valid M type identifiers.
# (TMDL's own `dataType: int64` lines are untouched: there's no `type ` keyword there.)
_M_TYPE_FIXES = {
    "string": "type text",
    "int64": "Int64.Type",
    "double": "type number",
    "decimal": "Currency.Type",
    "datetime": "type datetime",
    "boolean": "type logical",
    "binary": "type binary",
}
_M_TYPE_RE = re.compile(
    r"\btype\s+(string|int64|double|decimal|dateTime|boolean|binary)\b",
    re.IGNORECASE,
)


def _normalize_m_types(tmdl: str) -> str:
    """Rewrite invalid TMDL-style M type identifiers (type int64, ...) to valid M types."""
    return _M_TYPE_RE.sub(lambda m: _M_TYPE_FIXES[m.group(1).lower()], tmdl)


def _normalize_tmdl_indentation(tmdl: str) -> str:
    """Convert space-indented TMDL to tab-indented; M source blocks are left intact."""
    lines = tmdl.split("\n")
    # Fast path: nothing starts with a space.
    if not any(ln and ln[0] == " " for ln in lines):
        return tmdl

    # Find the smallest non-zero leading-space count on TMDL lines (skip M blocks).
    in_m = False
    indent_sizes: list[int] = []
    for ln in lines:
        stripped = ln.strip()
        if not in_m and "```" in ln and stripped.lstrip("source").lstrip().lstrip("=").lstrip().startswith("```"):
            in_m = True
            continue
        if in_m:
            if stripped == "```":
                in_m = False
            continue
        if ln and ln[0] == " ":
            indent_sizes.append(len(ln) - len(ln.lstrip(" ")))

    unit = min(indent_sizes) if indent_sizes else 4

    result: list[str] = []
    in_m = False
    for ln in lines:
        stripped = ln.strip()
        if not in_m and "```" in ln and stripped.lstrip("source").lstrip().lstrip("=").lstrip().startswith("```"):
            in_m = True
            # The `source = ``` line is itself a TMDL line — fix its indentation.
            if ln and ln[0] == " ":
                body = ln.lstrip(" ")
                tabs = (len(ln) - len(body)) // unit
                result.append("\t" * tabs + body)
            else:
                result.append(ln)
            continue
        if in_m:
            result.append(ln)  # preserve M source content as-is
            if stripped == "```":
                in_m = False
            continue
        if ln and ln[0] == " ":
            body = ln.lstrip(" ")
            tabs = (len(ln) - len(body)) // unit
            result.append("\t" * tabs + body)
        else:
            result.append(ln)
    return "\n".join(result)


# Partition mode values must be lowercase (or camelCase); LLMs often emit Title Case.
_TMDL_MODE_RE = re.compile(
    r"^(\s*mode\s*:\s*)(Import|DirectQuery|DualMode|Push|Streaming|IMPORT|DIRECTQUERY)(\s*)$",
    re.MULTILINE,
)
_MODE_MAP = {
    "Import": "import",
    "DirectQuery": "directQuery",
    "DualMode": "dualMode",
    "Push": "push",
    "Streaming": "streaming",
    "IMPORT": "import",
    "DIRECTQUERY": "directQuery",
}


def _normalize_tmdl_mode(tmdl: str) -> str:
    """Fix partition mode to correct TMDL casing (Import → import, etc.)."""
    return _TMDL_MODE_RE.sub(
        lambda m: m.group(1) + _MODE_MAP.get(m.group(2), m.group(2).lower()) + m.group(3),
        tmdl,
    )


def _sanitize_tmdl(tmdl: str) -> str:
    """Fix the common LLM TMDL/M mistakes that break Power BI Desktop on load."""
    tmdl = _normalize_tmdl_indentation(tmdl)
    tmdl = _normalize_tmdl_booleans(tmdl)
    tmdl = _normalize_m_types(tmdl)
    tmdl = _normalize_tmdl_mode(tmdl)
    return tmdl


def _load(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8")


def _profile_sources(profile: SchemaProfile) -> list[SourceInfo]:
    if profile.sources:
        return profile.sources
    return [SourceInfo(type=ConnectorType(profile.source_type), database=profile.database)]


def _schema_context(profile: SchemaProfile) -> str:
    """Compact, model-friendly rendering of the schema profile."""
    sources = _profile_sources(profile)
    multi = len(sources) > 1
    lines: list[str] = []
    if multi:
        lines.append(
            f"This report combines {len(sources)} data sources. Each table below is "
            "tagged with the source it came from; use that source's M template for the "
            "table's partition."
        )
        for s in sources:
            db = f", database/file: {s.database}" if s.database else ""
            lines.append(f'\nSOURCE [{s.index}] "{s.name}" ({s.type.value}{db})')
            lines.append(f"  M source template: {m_source_hint(s.type, s)}")
    else:
        s = sources[0]
        lines.append(f"Source type: {s.type.value}")
        if s.database:
            lines.append(f"Database: {s.database}")
        lines.append(f"M source template: {m_source_hint(s.type, s)}")
    if profile.truncated:
        lines.append("(Note: schema was truncated to fit the context budget.)")
    for t in profile.tables:
        src_note = ""
        if multi:
            src = sources[t.source_index] if t.source_index < len(sources) else sources[0]
            src_note = f' (source [{src.index}] "{src.name}")'
        lines.append(f"\nTABLE {t.name} (~{t.approx_row_count} rows){src_note}")
        for c in t.columns:
            flags = []
            if c.is_primary_key:
                flags.append("PK")
            if c.is_foreign_key:
                flags.append(f"FK->{c.fk_ref}")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            sample = f"  e.g. {c.sample_values[:3]}" if c.sample_values else ""
            lines.append(f"  - {c.name}: {c.data_type}{flag_str}{sample}")
    if profile.inferred_relationships:
        lines.append("\nRELATIONSHIPS:")
        for r in profile.inferred_relationships:
            lines.append(
                f"  {r.from_table}.{r.from_column} -> {r.to_table}.{r.to_column} ({r.source})"
            )
    return "\n".join(lines)


def build_messages(profile: SchemaProfile, user_request: str) -> list[dict]:
    system = _load("tmdl_system.txt")
    user = (
        f"SCHEMA CONTEXT:\n{_schema_context(profile)}\n\n"
        f"REPORT REQUEST:\n{user_request}\n\n"
        "Generate the TMDL semantic model now."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_repair_messages(
    base_messages: list[dict], current: dict, errors: list[str]
) -> list[dict]:
    repair = _load("tmdl_repair.txt").format(
        error_list="\n".join(f"- {e}" for e in errors),
        current_content=json.dumps(current, indent=2),
    )
    return base_messages + [{"role": "user", "content": repair}]


def parse_artifacts(raw: dict) -> SemanticModelArtifacts:
    return SemanticModelArtifacts(
        model_tmdl=_sanitize_tmdl(raw.get("model_tmdl", "")),
        tables={k: _sanitize_tmdl(v) for k, v in raw.get("tables", {}).items()},
        relationships_tmdl=_sanitize_tmdl(raw.get("relationships_tmdl", "")),
        expressions_tmdl=_sanitize_tmdl(raw.get("expressions_tmdl", "")),
    )


async def generate_semantic_model(
    llm: LLMRouter,
    profile: SchemaProfile,
    user_request: str,
) -> tuple[SemanticModelArtifacts, dict, list[dict]]:
    """Returns (artifacts, raw_json, base_messages) so the retry loop can build repairs."""
    messages = build_messages(profile, user_request)
    raw = await llm.complete_json(messages)
    return parse_artifacts(raw), raw, messages
