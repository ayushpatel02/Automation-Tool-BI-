"""M source templates and multi-source schema context rendering."""

from datetime import UTC, datetime

from app.generation.m_sources import m_source_hint
from app.generation.semantic_model import _schema_context
from app.schemas.connector import ConnectorType, SchemaProfile, SourceInfo, TableProfile


def test_m_source_hint_without_source_uses_placeholders():
    hint = m_source_hint(ConnectorType.POSTGRESQL)
    assert hint == (
        'let Source = PostgreSQL.Database("{host}", "{database}"), '
        'data = Source{[Schema="{schema}",Item="{table}"]}[Data] in data'
    )


def test_m_source_hint_substitutes_known_values():
    source = SourceInfo(
        index=0,
        name="pg",
        type=ConnectorType.POSTGRESQL,
        host="db.example.com",
        database="sales",
        schema_name="public",
    )
    hint = m_source_hint(ConnectorType.POSTGRESQL, source)
    assert hint == (
        'let Source = PostgreSQL.Database("db.example.com", "sales"), '
        'data = Source{[Schema="public",Item="{table}"]}[Data] in data'
    )


def test_m_source_hint_snowflake_substitutes_extra_fields():
    source = SourceInfo(
        index=0,
        name="sf",
        type=ConnectorType.SNOWFLAKE,
        host="abc123.snowflakecomputing.com",
        database="ANALYTICS",
        schema_name="PUBLIC",
        extra={"warehouse": "COMPUTE_WH"},
    )
    hint = m_source_hint(ConnectorType.SNOWFLAKE, source)
    assert "COMPUTE_WH" in hint
    assert "{warehouse}" not in hint
    # Table is filled in per-table by the model, so it stays a placeholder.
    assert "{table}" in hint


def test_schema_context_multi_source_mentions_each_source_and_table():
    profile = SchemaProfile(
        source_type="multiple",
        sources=[
            SourceInfo(index=0, name="Sales DB", type=ConnectorType.POSTGRESQL, host="db1", database="sales"),
            SourceInfo(
                index=1,
                name="Marketing CSV",
                type=ConnectorType.CSV,
                database="marketing.csv",
                extra={"file_path": "/data/marketing.csv"},
            ),
        ],
        tables=[
            TableProfile(name="Orders", source_index=0),
            TableProfile(name="Campaigns", source_index=1),
        ],
        profiled_at=datetime.now(UTC),
    )

    context = _schema_context(profile)

    assert "combines 2 data sources" in context
    assert 'SOURCE [0] "Sales DB"' in context
    assert 'SOURCE [1] "Marketing CSV"' in context
    assert 'TABLE Orders' in context and 'source [0] "Sales DB"' in context
    assert 'TABLE Campaigns' in context and 'source [1] "Marketing CSV"' in context
