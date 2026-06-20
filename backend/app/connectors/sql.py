"""SQLAlchemy-backed connectors: PostgreSQL, MySQL, SQL Server.

We use synchronous SQLAlchemy engines for schema introspection because the SQLAlchemy
``Inspector`` API (used by the profiler) is synchronous. Introspection is short-lived and
run in a worker thread by the profiler, so this does not block the event loop.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from app.connectors.base import DataConnector, register
from app.schemas.connector import ConnectionTestResult, ConnectorType

_DRIVERS = {
    ConnectorType.POSTGRESQL: ("postgresql+psycopg2", 5432),
    ConnectorType.MYSQL: ("mysql+pymysql", 3306),
    ConnectorType.SQLSERVER: ("mssql+pyodbc", 1433),
}


@register(ConnectorType.POSTGRESQL, ConnectorType.MYSQL, ConnectorType.SQLSERVER)
class SQLConnector(DataConnector):
    def sqlalchemy_url(self) -> str:
        driver, default_port = _DRIVERS[self.config.type]
        cfg = self.config
        user = quote_plus(cfg.username or "")
        pwd = quote_plus(cfg.password or "")
        host = cfg.host or "localhost"
        port = cfg.port or default_port
        db = cfg.database or ""
        auth = f"{user}:{pwd}@" if user else ""
        url = f"{driver}://{auth}{host}:{port}/{db}"
        if cfg.type == ConnectorType.SQLSERVER:
            # ODBC Driver 18 is the current cross-platform driver for SQL Server.
            driver_name = cfg.extra.get("odbc_driver", "ODBC Driver 18 for SQL Server")
            url += f"?driver={quote_plus(driver_name)}&TrustServerCertificate=yes"
        return url

    async def test_connection(self) -> ConnectionTestResult:
        import asyncio

        def _probe() -> ConnectionTestResult:
            from sqlalchemy import create_engine, text

            try:
                engine = create_engine(self.sqlalchemy_url(), pool_pre_ping=True)
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                engine.dispose()
                return ConnectionTestResult(ok=True, message="Connection successful")
            except Exception as exc:  # noqa: BLE001 — surface any driver error to the user
                return ConnectionTestResult(ok=False, message=str(exc))

        return await asyncio.to_thread(_probe)
