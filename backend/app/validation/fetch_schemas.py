"""Download and cache Microsoft's published PBIR JSON schemas for offline validation.

Run once at build time (or whenever schema versions change):

    python -m app.validation.fetch_schemas

VERIFY the version strings below against current Microsoft documentation — PBIR schema
versions may have incremented since this was written (June 2026).
"""

from __future__ import annotations

from pathlib import Path

import httpx

_BASE = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
_SCHEMA_DIR = Path(__file__).parent / "schemas"

# (fileType, version) — keep in sync with the $schema URLs emitted by Power BI Desktop.
_SCHEMAS: list[tuple[str, str]] = [
    ("report", "4.0.0"),
    ("page", "2.0.0"),
    ("visualContainer", "2.0.0"),
]


def fetch_all() -> None:
    _SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=30) as client:
        for file_type, version in _SCHEMAS:
            url = f"{_BASE}/{file_type}/{version}/schema.json"
            dest = _SCHEMA_DIR / f"{file_type}-{version}.json"
            try:
                resp = client.get(url)
                resp.raise_for_status()
                dest.write_text(resp.text, encoding="utf-8")
                print(f"cached {dest.name}")
            except Exception as exc:  # noqa: BLE001
                print(f"WARN: could not fetch {url}: {exc}")


if __name__ == "__main__":
    fetch_all()
