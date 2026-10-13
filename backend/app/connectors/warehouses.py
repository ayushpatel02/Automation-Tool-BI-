"""Cloud warehouse connectors: Snowflake, BigQuery, Databricks.

These rely on optional dependencies (install with the ``warehouses`` extra). The SQLAlchemy
URL builders let the profiler reuse the same Inspector-based introspection path as the SQL
connectors. Where a provider is not SQLAlchemy-friendly, the connector still implements
``test_connection`` and the profiler falls back to provider-native introspection.
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote_plus

from app.connectors.base import DataConnector, register
from app.schemas.connector import ConnectionTestResult, ConnectorType


@register(ConnectorType.SNOWFLAKE)
class SnowflakeConnector(DataConnector):
    def sqlalchemy_url(self) -> str:
        cfg = self.config
        account = cfg.extra.get("account", "")
        warehouse = cfg.extra.get("warehouse", "")
        role = cfg.extra.get("role", "")
        user = quote_plus(cfg.username or "")
        pwd = quote_plus(cfg.password or "")
        db = cfg.database or ""
        schema = cfg.schema_name or "PUBLIC"
        url = f"snowflake://{user}:{pwd}@{account}/{db}/{schema}"
        params = []
        if warehouse:
            params.append(f"warehouse={warehouse}")
        if role:
            params.append(f"role={role}")
        if params:
            url += "?" + "&".join(params)
        return url

    async def test_connection(self) -> ConnectionTestResult:
        return await _probe_sqlalchemy(self.sqlalchemy_url())


@register(ConnectorType.BIGQUERY)
class BigQueryConnector(DataConnector):
    def sqlalchemy_url(self) -> str:
        # sqlalchemy-bigquery uses bigquery://<project>/<dataset>. Auth via the
        # GOOGLE_APPLICATION_CREDENTIALS env var or an explicit credentials path in extra.
        project = self.config.extra.get("project", self.config.database or "")
        dataset = self.config.schema_name or ""
        return f"bigquery://{project}/{dataset}" if dataset else f"bigquery://{project}"

    async def test_connection(self) -> ConnectionTestResult:
        return await _probe_sqlalchemy(self.sqlalchemy_url())


@register(ConnectorType.DATABRICKS)
class DatabricksConnector(DataConnector):
    def sqlalchemy_url(self) -> str:
        cfg = self.config
        host = cfg.host or ""
        http_path = quote_plus(cfg.extra.get("http_path", ""))
        token = cfg.extra.get("access_token", cfg.password or "")
        catalog = cfg.database or ""
        schema = cfg.schema_name or "default"
        url = f"databricks://token:{token}@{host}?http_path={http_path}"
        if catalog:
            url += f"&catalog={catalog}&schema={schema}"
        return url

    async def test_connection(self) -> ConnectionTestResult:
        return await _probe_sqlalchemy(self.sqlalchemy_url())


async def _probe_sqlalchemy(url: str) -> ConnectionTestResult:
    def _probe() -> ConnectionTestResult:
        from sqlalchemy import create_engine, text

        try:
            engine = create_engine(url, pool_pre_ping=True)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            engine.dispose()
            return ConnectionTestResult(ok=True, message="Connection successful")
        except ModuleNotFoundError as exc:
            return ConnectionTestResult(
                ok=False,
                message=f"Driver not installed (pip install '.[warehouses]'): {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectionTestResult(ok=False, message=str(exc))

    return await asyncio.to_thread(_probe)
