"""Static PBIP boilerplate. These files are deterministic scaffolding, not AI-generated.

The shapes here follow the PBIP/PBIR layout documented by Microsoft. VERIFY exact field
names and versions against current Power BI Desktop output before shipping — the format is
recent and evolving.
"""

from __future__ import annotations

import json
import re
import uuid

# Base for all PBIR report-definition $schema URLs. Versions verified current (2026):
# a non-existent version (e.g. report/4.0.0, pagesMetadata/2.0.0) makes Power BI Desktop
# silently drop content, so each file below pins a known-published version.
_DEF_BASE = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"

VERSION_METADATA_SCHEMA = f"{_DEF_BASE}/versionMetadata/1.0.0/schema.json"
REPORT_SCHEMA = f"{_DEF_BASE}/report/3.0.0/schema.json"
PAGES_METADATA_SCHEMA = f"{_DEF_BASE}/pagesMetadata/1.0.0/schema.json"
PAGE_SCHEMA = f"{_DEF_BASE}/page/2.0.0/schema.json"
VISUAL_CONTAINER_SCHEMA = f"{_DEF_BASE}/visualContainer/2.4.0/schema.json"


def pbip_entry(project_name: str) -> str:
    return json.dumps(
        {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
            "version": "1.0",
            "artifacts": [{"report": {"path": f"{project_name}.Report"}}],
            "settings": {"enableAutoRecovery": True},
        },
        indent=2,
    )


def platform_file(display_name: str, item_type: str) -> str:
    return json.dumps(
        {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": item_type, "displayName": display_name},
            "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
        },
        indent=2,
    )


def definition_pbir(project_name: str) -> str:
    # version MUST be "4.0" or higher — that is what tells Power BI the report is stored
    # in the PBIR *definition-folder* format (pages/). With the old "1.0" value Power BI
    # treats it as a legacy single-file report, ignores the pages/ folder entirely, and
    # opens a blank "Page 1". definitionProperties schema is 2.0.0.
    return json.dumps(
        {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {
                "byPath": {"path": f"../{project_name}.SemanticModel"},
            },
        },
        indent=2,
    )


def definition_pbism() -> str:
    """The semantic model entry point (.SemanticModel/definition.pbism).

    Power BI Desktop requires this file alongside the TMDL `definition/` folder; without
    it, opening the project fails with "DatasetDefinition: Required artifact is missing".
    `version` 4.0+ tells Power BI the model is stored as TMDL (the `definition/` folder)
    rather than legacy TMSL (model.bim).
    """
    return json.dumps(
        {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
            "version": "4.2",
            "settings": {},
        },
        indent=2,
    )


def _tmdl_identifier(name: str) -> str:
    """Render a TMDL object name, single-quoting it when it isn't a bare identifier
    (e.g. contains spaces), as TMDL requires."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return name
    return "'" + name.replace("'", "''") + "'"


def database_tmdl(project_name: str) -> str:
    """database.tmdl — declares the database object + compatibility level.

    Required next to model.tmdl for a TMDL semantic model. Compatibility level 1567 is
    the current Power BI Desktop default for semantic models.
    """
    return f"database {_tmdl_identifier(project_name)}\n\tcompatibilityLevel: 1567\n"


def model_tmdl_header(project_name: str) -> str:
    """Minimal model.tmdl preamble if the model omits one."""
    return (
        "model Model\n"
        "\tculture: en-US\n"
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
    )


def version_json() -> str:
    """definition/version.json — REQUIRED. Without it Power BI Desktop recognizes the
    report but renders no visuals. Both fields required; version is major.minor.0."""
    return json.dumps(
        {"$schema": VERSION_METADATA_SCHEMA, "version": "2.0.0"}, indent=2
    )


def default_report_json() -> dict:
    return {
        "$schema": REPORT_SCHEMA,
        "themeCollection": {"baseTheme": {"name": "CY24SU10"}},
        "settings": {},
    }


def finalize_report_json(report_json: dict | None) -> dict:
    """Guarantee report.json carries the correct $schema and baseline keys.

    The report-level JSON is model-generated and often omits $schema (or pins a stale
    version), which makes Power BI reject the report definition. Force the known-good
    schema and fill any missing baseline keys without discarding the model's content.
    """
    rj = dict(report_json or {})
    rj["$schema"] = REPORT_SCHEMA  # force — the model's value is unreliable here
    rj.setdefault("themeCollection", {"baseTheme": {"name": "CY24SU10"}})
    rj.setdefault("settings", {})
    return rj


def default_page_json(page_id: str, display_name: str, ordinal: int) -> dict:
    return finalize_page_json(None, page_id, display_name, ordinal)


def finalize_page_json(
    page_json: dict | None, page_id: str, display_name: str, ordinal: int
) -> dict:
    """Guarantee page.json has every field Power BI requires to render the page's visuals.

    Required: $schema, name (must equal the page folder), displayName, displayOption
    (a STRING, not an int), width, height. A page missing displayOption or $schema loads
    as a blank canvas with its visuals dropped.
    """
    pj = dict(page_json or {})
    pj["$schema"] = PAGE_SCHEMA
    pj["name"] = page_id  # must match the containing folder name
    pj.setdefault("displayName", display_name)
    pj.setdefault("displayOption", "FitToPage")
    pj.setdefault("width", 1280)
    pj.setdefault("height", 720)
    pj.setdefault("ordinal", ordinal)
    return pj


def pages_order(page_ids: list[str]) -> dict:
    return {
        "$schema": PAGES_METADATA_SCHEMA,
        "pageOrder": page_ids,
        "activePageName": page_ids[0] if page_ids else "",
    }
