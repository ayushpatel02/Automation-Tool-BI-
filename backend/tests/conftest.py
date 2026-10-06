"""Shared pytest fixtures and sample artifacts."""

from __future__ import annotations

import pytest

from app.schemas.generation import ReportArtifacts, SemanticModelArtifacts


@pytest.fixture
def sample_model() -> SemanticModelArtifacts:
    sales = (
        "table Sales\n"
        "\tcolumn Amount\n"
        "\t\tdataType: decimal\n"
        "\t\tsourceColumn: Amount\n"
        "\tcolumn ProductId\n"
        "\t\tdataType: int64\n"
        "\t\tsourceColumn: ProductId\n"
        "\tmeasure 'Total Sales' = SUM(Sales[Amount])\n"
        "\tpartition Sales = m\n"
        "\t\tmode: import\n"
        "\t\tsource = let Source = 1 in Source\n"
    )
    products = (
        "table Products\n"
        "\tcolumn ProductId\n"
        "\t\tdataType: int64\n"
        "\t\tsourceColumn: ProductId\n"
        "\tcolumn Category\n"
        "\t\tdataType: string\n"
        "\t\tsourceColumn: Category\n"
        "\tpartition Products = m\n"
        "\t\tmode: import\n"
        "\t\tsource = let Source = 1 in Source\n"
    )
    return SemanticModelArtifacts(
        model_tmdl="model Model\n\tculture: en-US\n",
        tables={"Sales.tmdl": sales, "Products.tmdl": products},
        relationships_tmdl="relationship Sales_Products\n\tfromColumn: Sales.ProductId\n",
    )


def _measure_ref(entity: str, prop: str) -> dict:
    return {
        "Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}
    }


def _column_ref(entity: str, prop: str) -> dict:
    return {
        "Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}
    }


@pytest.fixture
def valid_report() -> ReportArtifacts:
    visual = {
        "name": "v1",
        "visual": {
            "visualType": "barChart",
            "query": {
                "queryState": {
                    "Category": {"projections": [{"field": _column_ref("Products", "Category")}]},
                    "Y": {"projections": [{"field": _measure_ref("Sales", "Total Sales")}]},
                }
            },
        },
    }
    return ReportArtifacts(
        report_json={"$schema": "report"},
        pages=[
            {
                "page_id": "ReportSection1",
                "page_json": {"name": "ReportSection1", "displayName": "Page 1"},
                "visuals": [{"visual_id": "v1", "visual_json": visual}],
            }
        ],
    )


@pytest.fixture
def invalid_report() -> ReportArtifacts:
    visual = {
        "name": "v1",
        "visual": {
            "visualType": "barChart",
            "query": {
                "queryState": {
                    "Y": {"projections": [{"field": _measure_ref("Sales", "Nonexistent")}]},
                }
            },
        },
    }
    return ReportArtifacts(
        report_json={},
        pages=[
            {
                "page_id": "ReportSection1",
                "page_json": {"name": "ReportSection1"},
                "visuals": [{"visual_id": "v1", "visual_json": visual}],
            }
        ],
    )
