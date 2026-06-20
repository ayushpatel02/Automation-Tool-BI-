"""Static PBIP boilerplate. These files are deterministic scaffolding, not AI-generated.

The shapes here follow the PBIP/PBIR layout documented by Microsoft. VERIFY exact field
names and versions against current Power BI Desktop output before shipping — the format is
recent and evolving.
"""

from __future__ import annotations

import json
import re
import uuid


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
    return json.dumps(
        {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/1.0.0/schema.json",
            "version": "1.0",
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


def default_report_json() -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/4.0.0/schema.json",
        "themeCollection": {"baseTheme": {"name": "CY24SU10"}},
    }


def default_page_json(page_id: str, display_name: str, ordinal: int) -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.0.0/schema.json",
        "name": page_id,
        "displayName": display_name,
        "displayOption": "FitToPage",
        "width": 1280,
        "height": 720,
        "ordinal": ordinal,
    }


def pages_order(page_ids: list[str]) -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/2.0.0/schema.json",
        "pageOrder": page_ids,
        "activePageName": page_ids[0] if page_ids else "",
    }
