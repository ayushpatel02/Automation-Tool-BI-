"""Stage 1: generate the TMDL semantic model from the schema profile + user request."""

from __future__ import annotations

import json
from pathlib import Path

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
        model_tmdl=raw.get("model_tmdl", ""),
        tables=raw.get("tables", {}),
        relationships_tmdl=raw.get("relationships_tmdl", ""),
        expressions_tmdl=raw.get("expressions_tmdl", ""),
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
