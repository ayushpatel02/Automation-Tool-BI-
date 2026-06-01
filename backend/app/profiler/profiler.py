"""Schema profiler: introspect tables/columns/keys/cardinality/sample rows.

Two introspection paths:

* SQLAlchemy ``Inspector`` for engine-backed connectors (SQL databases + warehouses that
  expose a SQLAlchemy dialect).
* pandas for CSV/Excel file connectors.

The result is truncated to fit a token budget so the prompt to the LLM stays bounded for
very large schemas. Most-connected tables (by FK reference count) are kept first.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

from app.config import get_settings
from app.connectors.base import DataConnector
from app.connectors.files import FileConnector
from app.schemas.connector import (
    ColumnProfile,
    NormalizedType,
    RelationshipHint,
    SchemaProfile,
    TableProfile,
)

settings = get_settings()


def _normalize_type(raw: str) -> NormalizedType:
    r = raw.lower()
    if any(k in r for k in ("char", "text", "string", "uuid", "json", "enum", "clob")):
        return "string"
    if any(k in r for k in ("bool", "bit")):
        return "boolean"
    if any(k in r for k in ("int", "serial")):
        return "integer"
    if any(k in r for k in ("float", "double", "real", "decimal", "numeric", "money")):
        return "decimal"
    if any(k in r for k in ("date", "time", "timestamp")):
        return "datetime"
    if any(k in r for k in ("blob", "binary", "bytea")):
        return "binary"
    return "string"


def _rough_tokens(text: str) -> int:
    # ~4 chars per token is a serviceable heuristic for budgeting.
    return max(1, len(text) // 4)


async def profile_schema(
    connector: DataConnector,
    *,
    selected_tables: list[str] | None = None,
    max_tables: int = 40,
    max_columns_per_table: int = 25,
    sample_rows: int = 5,
) -> SchemaProfile:
    if isinstance(connector, FileConnector):
        profile = await asyncio.to_thread(
            _profile_file, connector, max_columns_per_table, sample_rows
        )
    else:
        profile = await asyncio.to_thread(
            _profile_sql,
            connector,
            selected_tables,
            max_tables,
            max_columns_per_table,
            sample_rows,
        )
    _apply_token_budget(profile, settings.schema_token_budget)
    return profile


# --- SQLAlchemy path -------------------------------------------------------

def _profile_sql(
    connector: DataConnector,
    selected_tables: list[str] | None,
    max_tables: int,
    max_columns_per_table: int,
    sample_rows: int,
) -> SchemaProfile:
    from sqlalchemy import create_engine, inspect, text

    url = connector.sqlalchemy_url()
    if url is None:
        raise ValueError("Connector does not expose a SQLAlchemy URL")

    engine = create_engine(url, pool_pre_ping=True)
    tables: list[TableProfile] = []
    relationships: list[RelationshipHint] = []
    try:
        inspector = inspect(engine)
        schema = connector.config.schema_name
        table_names = inspector.get_table_names(schema=schema)
        if selected_tables:
            table_names = [t for t in table_names if t in selected_tables]
        table_names = table_names[:max_tables]

        with engine.connect() as conn:
            for tname in table_names:
                pk_cols = set(
                    inspector.get_pk_constraint(tname, schema=schema).get(
                        "constrained_columns", []
                    )
                )
                fk_map: dict[str, str] = {}
                for fk in inspector.get_foreign_keys(tname, schema=schema):
                    ref_table = fk.get("referred_table")
                    for local, remote in zip(
                        fk.get("constrained_columns", []),
                        fk.get("referred_columns", []),
                        strict=False,
                    ):
                        fk_map[local] = f"{ref_table}.{remote}"
                        relationships.append(
                            RelationshipHint(
                                from_table=tname,
                                from_column=local,
                                to_table=str(ref_table),
                                to_column=remote,
                                source="foreign_key",
                            )
                        )

                columns: list[ColumnProfile] = []
                raw_cols = inspector.get_columns(tname, schema=schema)
                samples = _sample_rows(conn, text, tname, schema, sample_rows)
                for col in raw_cols[:max_columns_per_table]:
                    cname = col["name"]
                    columns.append(
                        ColumnProfile(
                            name=cname,
                            data_type=_normalize_type(str(col["type"])),
                            raw_type=str(col["type"]),
                            nullable=bool(col.get("nullable", True)),
                            is_primary_key=cname in pk_cols,
                            is_foreign_key=cname in fk_map,
                            fk_ref=fk_map.get(cname),
                            sample_values=[
                                str(row.get(cname))[:50]
                                for row in samples
                                if row.get(cname) is not None
                            ][:5],
                        )
                    )

                row_count = _count_rows(conn, text, tname, schema)
                tables.append(
                    TableProfile(
                        name=tname,
                        schema_name=schema,
                        approx_row_count=row_count,
                        columns=columns,
                    )
                )
    finally:
        engine.dispose()

    relationships.extend(_infer_relationships_by_naming(tables))
    return SchemaProfile(
        source_type=connector.config.type.value,
        database=connector.config.database,
        tables=tables,
        inferred_relationships=_dedupe_relationships(relationships),
        profiled_at=datetime.now(UTC),
    )


def _sample_rows(conn, text, tname, schema, n) -> list[dict]:
    fq = f'"{schema}"."{tname}"' if schema else f'"{tname}"'
    try:
        result = conn.execute(text(f"SELECT * FROM {fq} LIMIT {n}"))
        return [dict(r._mapping) for r in result]
    except Exception:  # noqa: BLE001 — sampling is best-effort
        return []


def _count_rows(conn, text, tname, schema) -> int:
    fq = f'"{schema}"."{tname}"' if schema else f'"{tname}"'
    try:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {fq}")).scalar() or 0)
    except Exception:  # noqa: BLE001
        return 0


# --- File path -------------------------------------------------------------

def _profile_file(
    connector: FileConnector, max_columns_per_table: int, sample_rows: int
) -> SchemaProfile:
    import pandas as pd

    path = connector.file_path
    table_name = re.sub(r"[^A-Za-z0-9_]", "_", path.stem)
    if connector.config.type.value == "excel":
        df = pd.read_excel(path, nrows=1000)
    else:
        df = pd.read_csv(path, nrows=1000)

    columns: list[ColumnProfile] = []
    for cname in list(df.columns)[:max_columns_per_table]:
        series = df[cname]
        columns.append(
            ColumnProfile(
                name=str(cname),
                data_type=_normalize_type(str(series.dtype)),
                raw_type=str(series.dtype),
                nullable=bool(series.isnull().any()),
                approx_cardinality=int(series.nunique()),
                sample_values=[str(v)[:50] for v in series.dropna().head(5).tolist()],
            )
        )

    table = TableProfile(
        name=table_name, approx_row_count=int(len(df)), columns=columns
    )
    return SchemaProfile(
        source_type=connector.config.type.value,
        database=path.name,
        tables=[table],
        profiled_at=datetime.now(UTC),
    )


# --- Relationship inference + budgeting ------------------------------------

def _infer_relationships_by_naming(tables: list[TableProfile]) -> list[RelationshipHint]:
    """Infer FKs from `<table>_id` / `<table>id` naming when no DB constraint exists."""
    hints: list[RelationshipHint] = []
    pk_by_table = {
        t.name.lower(): next((c.name for c in t.columns if c.is_primary_key), None)
        for t in tables
    }
    for t in tables:
        for col in t.columns:
            if col.is_primary_key or col.is_foreign_key:
                continue
            m = re.match(r"^(.*?)_?id$", col.name, re.IGNORECASE)
            if not m:
                continue
            base = m.group(1).lower()
            for cand in (base, base + "s"):
                if cand in pk_by_table and pk_by_table[cand]:
                    target = next(t2 for t2 in tables if t2.name.lower() == cand)
                    hints.append(
                        RelationshipHint(
                            from_table=t.name,
                            from_column=col.name,
                            to_table=target.name,
                            to_column=pk_by_table[cand],
                            source="naming_convention",
                        )
                    )
                    break
    return hints


def _dedupe_relationships(rels: list[RelationshipHint]) -> list[RelationshipHint]:
    seen: set[tuple] = set()
    out: list[RelationshipHint] = []
    for r in rels:
        key = (r.from_table, r.from_column, r.to_table, r.to_column)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _apply_token_budget(profile: SchemaProfile, budget: int) -> None:
    """Drop least-connected tables until the serialized profile fits the token budget."""
    fk_counts: dict[str, int] = {t.name: 0 for t in profile.tables}
    for r in profile.inferred_relationships:
        fk_counts[r.from_table] = fk_counts.get(r.from_table, 0) + 1
        fk_counts[r.to_table] = fk_counts.get(r.to_table, 0) + 1

    profile.tables.sort(key=lambda t: fk_counts.get(t.name, 0), reverse=True)

    def estimate() -> int:
        return _rough_tokens(profile.model_dump_json())

    profile.token_estimate = estimate()
    while profile.token_estimate > budget and len(profile.tables) > 1:
        dropped = profile.tables.pop()
        profile.inferred_relationships = [
            r
            for r in profile.inferred_relationships
            if dropped.name not in (r.from_table, r.to_table)
        ]
        profile.truncated = True
        profile.token_estimate = estimate()
