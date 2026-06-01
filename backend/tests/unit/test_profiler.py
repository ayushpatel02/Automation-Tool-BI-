"""Profiler against a real (in-memory + temp file) data source, no mocks."""

import csv

import pytest

from app.connectors import get_connector
from app.profiler import profile_schema
from app.profiler.profiler import _normalize_type
from app.schemas.connector import ConnectorConfig, ConnectorType


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
