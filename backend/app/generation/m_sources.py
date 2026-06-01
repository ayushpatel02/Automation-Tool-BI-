"""Power Query (M) source templates per connector type.

The plan calls out that LLMs are unreliable at connector-specific M source syntax, so we
supply a deterministic template per source rather than letting the model guess. The
template is injected into the TMDL generation prompt and the model fills table names in.
"""

from __future__ import annotations

from app.schemas.connector import ConnectorType

# {server}/{database}/{schema}/{table} are filled by the model per table.
_TEMPLATES: dict[ConnectorType, str] = {
    ConnectorType.POSTGRESQL: (
        'let Source = PostgreSQL.Database("{host}", "{database}"), '
        'data = Source{{[Schema="{schema}",Item="{table}"]}}[Data] in data'
    ),
    ConnectorType.MYSQL: (
        'let Source = MySQL.Database("{host}", "{database}"), '
        'data = Source{{[Schema="{schema}",Item="{table}"]}}[Data] in data'
    ),
    ConnectorType.SQLSERVER: (
        'let Source = Sql.Database("{host}", "{database}"), '
        'data = Source{{[Schema="{schema}",Item="{table}"]}}[Data] in data'
    ),
    ConnectorType.SNOWFLAKE: (
        'let Source = Snowflake.Databases("{host}", "{warehouse}"), '
        'db = Source{{[Name="{database}",Kind="Database"]}}[Data], '
        'schema = db{{[Name="{schema}",Kind="Schema"]}}[Data], '
        'data = schema{{[Name="{table}",Kind="Table"]}}[Data] in data'
    ),
    ConnectorType.BIGQUERY: (
        'let Source = GoogleBigQuery.Database(), '
        'project = Source{{[Name="{database}"]}}[Data], '
        'dataset = project{{[Name="{schema}"]}}[Data], '
        'data = dataset{{[Name="{table}"]}}[Data] in data'
    ),
    ConnectorType.DATABRICKS: (
        'let Source = Databricks.Catalogs("{host}", "{http_path}", null), '
        'catalog = Source{{[Name="{database}",Kind="Database"]}}[Data], '
        'schema = catalog{{[Name="{schema}",Kind="Schema"]}}[Data], '
        'data = schema{{[Name="{table}",Kind="Table"]}}[Data] in data'
    ),
    ConnectorType.CSV: (
        'let Source = Csv.Document(File.Contents("{file_path}"), '
        '[Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]), '
        'promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]) in promoted'
    ),
    ConnectorType.EXCEL: (
        'let Source = Excel.Workbook(File.Contents("{file_path}"), null, true), '
        'sheet = Source{{[Kind="Sheet"]}}[Data], '
        'promoted = Table.PromoteHeaders(sheet, [PromoteAllScalars=true]) in promoted'
    ),
}


def m_source_hint(connector_type: ConnectorType, config_extra: dict | None = None) -> str:
    """Return the M source template string for the connector, as guidance for the prompt."""
    template = _TEMPLATES.get(connector_type)
    if template is None:
        return "Use the appropriate Power Query connector function for this source."
    return template
