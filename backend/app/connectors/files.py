"""File connectors: CSV and Excel.

These have no live engine; the profiler reads them directly with pandas. The TMDL M
partition for a file source loads the data inline (the path is recorded so Power BI Desktop
can refresh it locally).
"""

from __future__ import annotations

from pathlib import Path

from app.connectors.base import DataConnector, register
from app.schemas.connector import ConnectionTestResult, ConnectorType


@register(ConnectorType.CSV, ConnectorType.EXCEL)
class FileConnector(DataConnector):
    @property
    def file_path(self) -> Path:
        path = self.config.extra.get("file_path")
        if not path:
            raise ValueError("File connector requires extra['file_path']")
        return Path(path)

    async def test_connection(self) -> ConnectionTestResult:
        try:
            p = self.file_path
            if not p.exists():
                return ConnectionTestResult(ok=False, message=f"File not found: {p}")
            return ConnectionTestResult(ok=True, message=f"File readable: {p.name}")
        except Exception as exc:  # noqa: BLE001
            return ConnectionTestResult(ok=False, message=str(exc))
