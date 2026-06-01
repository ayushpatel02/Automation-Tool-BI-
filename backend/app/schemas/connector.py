"""Connector configuration and schema-profile domain models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ConnectorType(StrEnum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SQLSERVER = "sqlserver"
    SNOWFLAKE = "snowflake"
    BIGQUERY = "bigquery"
    DATABRICKS = "databricks"
    CSV = "csv"
    EXCEL = "excel"


class ConnectorConfig(BaseModel):
    """Full connection configuration. Secret fields are encrypted at rest."""

    type: ConnectorType
    name: str = "My data source"
    host: str | None = None
    port: int | None = None
    database: str | None = None
    schema_name: str | None = None
    username: str | None = None
    password: str | None = None
    # Warehouse/file-specific extras (account, warehouse, project, http_path, file_path, ...).
    extra: dict[str, str] = Field(default_factory=dict)


class ConnectionTestResult(BaseModel):
    ok: bool
    message: str
    server_version: str | None = None


# --- Schema profile -------------------------------------------------------

NormalizedType = Literal[
    "string", "integer", "decimal", "datetime", "boolean", "binary"
]


class ColumnProfile(BaseModel):
    name: str
    data_type: NormalizedType
    raw_type: str
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    fk_ref: str | None = None  # "TableName.ColumnName"
    approx_cardinality: int = 0
    sample_values: list[str] = Field(default_factory=list)


class TableProfile(BaseModel):
    name: str
    schema_name: str | None = None
    approx_row_count: int = 0
    columns: list[ColumnProfile] = Field(default_factory=list)


class RelationshipHint(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    source: Literal["foreign_key", "naming_convention"] = "foreign_key"


class SchemaProfile(BaseModel):
    source_type: str
    database: str | None = None
    tables: list[TableProfile] = Field(default_factory=list)
    inferred_relationships: list[RelationshipHint] = Field(default_factory=list)
    profiled_at: datetime
    token_estimate: int = 0
    truncated: bool = False


class StoredCredentialResponse(BaseModel):
    id: str
    name: str
    connector_type: str
    created_at: datetime
