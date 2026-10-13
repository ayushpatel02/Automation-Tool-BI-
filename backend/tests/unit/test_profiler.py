"""Profiler against a real (in-memory + temp file) data source, no mocks."""

import csv
from datetime import UTC, datetime

import pytest

from app.connectors import get_connector
from app.profiler import merge_profiles, profile_schema
from app.profiler.profiler import _normalize_type
from app.schemas.connector import (
    ConnectorConfig,
    ConnectorType,
    RelationshipHint,
    SchemaProfile,
    SourceInfo,
    TableProfile,
)


def test_type_normalization():
    assert _normalize_type("VARCHAR(50)") == "string"
    assert _normalize_type("INTEGER") == "integer"
    assert _normalize_type("NUMERIC(10,2)") == "decimal"
    assert _normalize_type("TIMESTAMP") == "datetime"
    assert _normalize_type("BOOLEAN") == "boolean"


@pytest.mark.asyncio
async def test_profile_csv(tmp_path):
    csv_path = tmp_path / "sales.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["OrderId", "Amount", "Category"])
        writer.writerow([1, 10.5, "A"])
        writer.writerow([2, 20.0, "B"])

    config = ConnectorConfig(
        type=ConnectorType.CSV, name="csv", extra={"file_path": str(csv_path)}
    )
    connector = get_connector(config)
    profile = await profile_schema(connector)

    assert profile.source_type == "csv"
    assert len(profile.tables) == 1
    table = profile.tables[0]
    assert table.name == "sales"
    col_names = {c.name for c in table.columns}
    assert {"OrderId", "Amount", "Category"} <= col_names


def _profile(source_name: str, source_type: ConnectorType, tables: list[TableProfile], **kw) -> SchemaProfile:
    return SchemaProfile(
        source_type=source_type.value,
        sources=[SourceInfo(index=0, name=source_name, type=source_type, **kw)],
        tables=tables,
        profiled_at=datetime.now(UTC),
    )


def test_merge_profiles_single_is_passthrough():
    profile = _profile("pg", ConnectorType.POSTGRESQL, [TableProfile(name="Sales")])
    assert merge_profiles([profile]) is profile


def test_merge_profiles_combines_sources_and_tags_tables():
    p1 = _profile(
        "Sales DB", ConnectorType.POSTGRESQL, [TableProfile(name="Sales"), TableProfile(name="Products")],
        database="db1",
    )
    p2 = _profile(
        "Marketing CSV", ConnectorType.CSV, [TableProfile(name="Campaigns")], database="marketing.csv"
    )

    merged = merge_profiles([p1, p2])

    assert merged.source_type == "multiple"
    assert [s.index for s in merged.sources] == [0, 1]
    assert {s.name for s in merged.sources} == {"Sales DB", "Marketing CSV"}
    by_name = {t.name: t.source_index for t in merged.tables}
    assert by_name == {"Sales": 0, "Products": 0, "Campaigns": 1}


def test_merge_profiles_disambiguates_collisions_and_renames_relationships():
    p1 = _profile(
        "Sales DB", ConnectorType.POSTGRESQL,
        [TableProfile(name="Customers"), TableProfile(name="Orders")],
        database="db1",
    )
    p2 = _profile(
        "CRM DB", ConnectorType.POSTGRESQL,
        [TableProfile(name="Customers"), TableProfile(name="Contacts")],
        database="db2",
    )
    p2.inferred_relationships = [
        RelationshipHint(
            from_table="Contacts", from_column="CustomerId", to_table="Customers", to_column="Id"
        )
    ]

    merged = merge_profiles([p1, p2])

    assert {t.name for t in merged.tables} == {"Customers", "Orders", "CRM_DB_Customers", "Contacts"}

    rel = merged.inferred_relationships[0]
    assert rel.from_table == "Contacts"
    assert rel.to_table == "CRM_DB_Customers"
