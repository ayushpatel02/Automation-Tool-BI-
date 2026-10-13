"""Stage 1: generate the TMDL semantic model from the schema profile + user request."""

from __future__ import annotations

import base64
import gzip
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


# `defaultPowerBIDataSourceVersion` is a TMDL enum (powerBI_V1/V2/V3), not a number. LLMs
# often emit `3.0`, producing "Failed to convert the value '3.0' to the expected type
# PowerBIDataSourceVersion" on PBI Desktop load. Map numeric values to the enum.
_TMDL_DSV_RE = re.compile(
    r"^(\s*defaultPowerBIDataSourceVersion\s*:\s*)(\S+)(\s*)$",
    re.MULTILINE,
)
_DSV_MAP = {
    "3.0": "powerBI_V3", "3": "powerBI_V3",
    "2.0": "powerBI_V2", "2": "powerBI_V2",
    "1.0": "powerBI_V1", "1": "powerBI_V1",
}


def _normalize_datasource_version(tmdl: str) -> str:
    """Map a numeric defaultPowerBIDataSourceVersion (3.0) to its TMDL enum (powerBI_V3)."""
    def repl(m: re.Match) -> str:
        val = m.group(2).strip().strip('"')
        if val.startswith("powerBI_V"):
            return m.group(0)  # already a valid enum
        return m.group(1) + _DSV_MAP.get(val, "powerBI_V3") + m.group(3)

    return _TMDL_DSV_RE.sub(repl, tmdl)


def _strip_tmdl_comments(tmdl: str) -> str:
    """Remove ``//`` line comments from TMDL (but not from inside M source blocks).

    TMDL has NO comment syntax — ``//`` at the start of a TMDL line causes
    "Unexpected line type: Other!" on Power BI Desktop load. Power Query M *does*
    support ``//`` comments, so lines inside a fenced M block (between ``source = ``` ``
    and the closing ````` ``) are left intact.
    """
    lines = tmdl.split("\n")
    result: list[str] = []
    in_m = False
    for ln in lines:
        stripped = ln.strip()
        if not in_m and stripped.startswith("source") and "```" in ln:
            in_m = True
            result.append(ln)
            continue
        if in_m:
            result.append(ln)
            if stripped == "```":
                in_m = False
            continue
        if stripped.startswith("//"):
            continue  # drop the TMDL comment line
        result.append(ln)
    return "\n".join(result)


def _sanitize_tmdl(tmdl: str) -> str:
    """Fix the common LLM TMDL/M mistakes that break Power BI Desktop on load."""
    tmdl = _strip_tmdl_comments(tmdl)
    tmdl = _normalize_tmdl_indentation(tmdl)
    tmdl = _normalize_tmdl_booleans(tmdl)
    tmdl = _normalize_m_types(tmdl)
    tmdl = _normalize_tmdl_mode(tmdl)
    tmdl = _normalize_datasource_version(tmdl)
    return tmdl


def sanitize_model(model: SemanticModelArtifacts) -> SemanticModelArtifacts:
    """Re-apply the deterministic TMDL normalizers across every artifact in a model.

    Used by the self-test's auto-repair step to fix the ``fix=\"auto\"`` findings.
    """
    return SemanticModelArtifacts(
        model_tmdl=_sanitize_tmdl(model.model_tmdl),
        tables={k: _sanitize_tmdl(v) for k, v in model.tables.items()},
        relationships_tmdl=_sanitize_tmdl(model.relationships_tmdl),
        expressions_tmdl=_sanitize_tmdl(model.expressions_tmdl),
    )


# --- File-source patching (CSV / Excel) ---------------------------------------
# CSV/Excel partitions are generated with `File.Contents("<name>")`. Power Query's
# File.Contents REQUIRES an absolute path that exists on the machine opening the
# .pbip — neither a bare filename nor the backend's server path works. The portable
# fix is to embed the file's bytes directly in the M expression so the .pbip is fully
# self-contained and loads with ZERO external path. patch_file_sources() does this
# once, after all generation/repair cycles (so the blob never bloats a repair prompt).
#
# Power BI rejects a model whose M query definitions exceed 10 MB ("We were unable to
# update the queries because they exceed the size limit of 10MB"). A raw Base64 embed
# inflates bytes by 4/3, so even a ~7.5 MB file overflows. To embed far larger files we
# GZIP-compress the bytes first, then Base64 them, and decompress in M:
#
#     Binary.Decompress(Binary.FromText("<b64>", BinaryEncoding.Base64), Compression.GZip)
#
# CSV / text data compresses ~5-10x, so a multi-MB CSV embeds in well under 10 MB and
# needs no path at all. Only a file that stays over the budget even after compression
# (rare: an already-compressed blob tens of MB in size) falls back to being bundled in
# the zip with an absolute placeholder path.

_FILE_CONTENTS_RE = re.compile(r'\bFile\.Contents\("([^"]*)"\)')
_BINARY_FROMTEXT_RE = re.compile(r'Binary\.FromText\("([^"]*)"')
# Power BI's hard limit on the combined size of all M query definitions.
_QUERY_SIZE_LIMIT_BYTES = 10 * 1024 * 1024  # 10 MB
# Max TOTAL Base64 we will embed across the whole model. Kept under the 10 MB query
# limit with headroom for the surrounding M scaffolding (let/in, Csv.Document, column
# types, other tables). Because we gzip first, this budget covers source files many
# times larger than 9 MB.
_EMBED_BUDGET_BYTES = 9 * 1024 * 1024  # 9 MB of (compressed) Base64
# Don't even read+compress a file larger than this (memory/CPU guard); bundle it.
_MAX_EMBED_SOURCE_BYTES = 80 * 1024 * 1024  # 80 MB
# Placeholder absolute path for the rare file too large to embed. It is absolute (so it
# does not trigger the "must be a valid absolute path" error) and obvious.
_PLACEHOLDER_DIR = "C:\\PowerBI-Data\\"


def _b64_len(raw_bytes: int) -> int:
    """Predicted Base64 length for *raw_bytes* bytes (4 chars per 3 bytes).

    Kept as a small utility for predicting encoded size; the embed path measures the
    actual compressed Base64 length rather than relying on this.
    """
    return 4 * ((raw_bytes + 2) // 3)


def _gzip_b64_embed(raw: bytes) -> str:
    """Return an M expression that reconstructs *raw* bytes inline (gzip + Base64).

    Pairs Python ``gzip.compress`` (which emits a gzip stream with header/footer) with
    Power Query ``Compression.GZip``, so the round-trip is exact. ``mtime=0`` keeps the
    output deterministic (no embedded timestamp), so re-running generation on the same
    file yields byte-identical TMDL.
    """
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    b64 = base64.b64encode(compressed).decode()
    return (
        f'Binary.Decompress(Binary.FromText("{b64}", BinaryEncoding.Base64), '
        "Compression.GZip)"
    )


def _file_sources(profile: SchemaProfile) -> list[SourceInfo]:
    """Every CSV/Excel source on the profile that carries a usable file path."""
    return [
        s
        for s in _profile_sources(profile)
        if s.type in (ConnectorType.CSV, ConnectorType.EXCEL) and s.extra.get("file_path")
    ]


def _source_display(src: SourceInfo) -> str:
    return src.extra.get("original_name") or Path(src.extra["file_path"]).name


def _resolve_file(ref: str, file_map: dict[str, str], sole_path: str | None) -> str | None:
    """Map a File.Contents("<ref>") argument to a server file path, as robustly as possible.

    The LLM may put the display name, a basename, a stem, or even garbage inside
    File.Contents(...). When there is exactly one file source, any File.Contents call
    must refer to it — so we fall back to that path unconditionally.
    """
    if ref in file_map:
        return file_map[ref]
    base = ref.replace("\\", "/").split("/")[-1]
    if base in file_map:
        return file_map[base]
    for disp, path in file_map.items():
        if base == disp or base.endswith(disp) or disp.endswith(base):
            return path
        if Path(base).stem.lower() == Path(disp).stem.lower():
            return path
    # Single file source: any File.Contents must be it, whatever string was written.
    return sole_path


def patch_file_sources(
    artifacts: SemanticModelArtifacts,
    profile: SchemaProfile,
) -> tuple[SemanticModelArtifacts, dict[str, str]]:
    """Embed CSV/Excel files as Base64 in M so the .pbip loads without an external path.

    Returns ``(patched_artifacts, data_files)`` where *data_files* maps
    ``display_name → server_path`` for any file too large to embed; those are copied
    into the download zip and the M reference is rewritten to an absolute placeholder.

    Call this once, just before creating the final zip — never during the repair loop,
    since an embedded Base64 blob would balloon the repair-prompt context.
    """
    sources = _file_sources(profile)
    if not sources:
        return artifacts, {}

    file_map = {_source_display(s): s.extra["file_path"] for s in sources}
    # The dominant case is a single uploaded file → a single table.
    sole_path = sources[0].extra["file_path"] if len(sources) == 1 else None
    path_to_display = {v: k for k, v in file_map.items()}

    data_files: dict[str, str] = {}
    new_tables: dict[str, str] = {}
    embedded_b64 = 0  # running total of Base64 chars already committed to the model
    embed_cache: dict[str, str | None] = {}  # server_path -> M embed expr (or None)

    def embed_for(server_file: Path) -> str | None:
        """Return the gzip+Base64 M expression for *server_file*, or None if it can't
        be embedded within the remaining budget. Memoized per path."""
        nonlocal embedded_b64
        key = str(server_file)
        if key in embed_cache:
            return embed_cache[key]
        size = server_file.stat().st_size
        if size > _MAX_EMBED_SOURCE_BYTES:
            embed_cache[key] = None
            return None
        expr = _gzip_b64_embed(server_file.read_bytes())
        b64_len = len(_BINARY_FROMTEXT_RE.search(expr).group(1))
        if embedded_b64 + b64_len > _EMBED_BUDGET_BYTES:
            embed_cache[key] = None
            return None
        embedded_b64 += b64_len
        embed_cache[key] = expr
        return expr

    for fname, content in artifacts.tables.items():
        if "Binary.FromText(" in content:
            # Already embedded (idempotency guard for re-tests / refinement re-runs).
            # Count its payload against the budget so a later table can't overflow.
            embedded_b64 += sum(
                len(mm.group(1)) for mm in _BINARY_FROMTEXT_RE.finditer(content)
            )
            new_tables[fname] = content
            continue

        new_content = content
        # Iterate in reverse so earlier match offsets stay valid after replacement.
        for m in reversed(list(_FILE_CONTENTS_RE.finditer(content))):
            ref = m.group(1)
            server_path = _resolve_file(ref, file_map, sole_path)
            if not server_path:
                # Cannot identify which file this is (ambiguous multi-source case).
                continue
            server_file = Path(server_path)
            display = path_to_display.get(server_path, server_file.name)

            embed_expr = embed_for(server_file) if server_file.exists() else None
            if embed_expr is not None:
                # Self-contained: data reconstructed inline, no external path at all.
                replacement = embed_expr
            elif server_file.exists():
                # Too large to embed even compressed: bundle the file and point at an
                # ABSOLUTE placeholder so Power BI doesn't reject it as a relative path.
                data_files[display] = server_path
                replacement = f'File.Contents("{_PLACEHOLDER_DIR}{display}")'
            else:
                # Server file not found (deleted/moved between upload and generation):
                # still replace the relative path with an absolute placeholder so the
                # error in Power BI Desktop is "file not found at C:\PowerBI-Data\..."
                # rather than the harder-to-diagnose "must be a valid absolute path".
                replacement = f'File.Contents("{_PLACEHOLDER_DIR}{display}")'

            new_content = new_content[: m.start()] + replacement + new_content[m.end() :]
        new_tables[fname] = new_content

    return artifacts.model_copy(update={"tables": new_tables}), data_files


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
