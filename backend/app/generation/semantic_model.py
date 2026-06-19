"""Stage 1: generate the TMDL semantic model from the schema profile + user request."""

from __future__ import annotations

import json
import re
from pathlib import Path

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

from app.generation.m_sources import m_source_hint
from app.llm.router import LLMRouter
from app.schemas.connector import ConnectorType, SchemaProfile, SourceInfo
from app.schemas.generation import SemanticModelArtifacts

_PROMPTS = Path(__file__).parent / "prompts"


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
        model_tmdl=_normalize_tmdl_booleans(raw.get("model_tmdl", "")),
        tables={
            k: _normalize_tmdl_booleans(v) for k, v in raw.get("tables", {}).items()
        },
        relationships_tmdl=_normalize_tmdl_booleans(raw.get("relationships_tmdl", "")),
        expressions_tmdl=_normalize_tmdl_booleans(raw.get("expressions_tmdl", "")),
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
