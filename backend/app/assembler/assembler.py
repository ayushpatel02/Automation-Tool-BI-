"""Assemble generated TMDL + PBIR artifacts into a valid .pbip folder tree, then zip it."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from app.assembler import templates as tpl
from app.schemas.generation import ReportArtifacts, SemanticModelArtifacts


def assemble_pbip(
    project_name: str,
    semantic_model: SemanticModelArtifacts,
    report: ReportArtifacts,
    output_dir: Path,
) -> Path:
    """Write the full .pbip tree under output_dir and return the project root path."""
    root = output_dir / project_name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    # Project entry point
    (output_dir / f"{project_name}.pbip").write_text(
        tpl.pbip_entry(project_name), encoding="utf-8"
    )

    _write_semantic_model(project_name, semantic_model, root)
    _write_report(project_name, report, root)
    return root


def _write_semantic_model(
    project_name: str, model: SemanticModelArtifacts, root: Path
) -> None:
    sm = root / f"{project_name}.SemanticModel"
    definition = sm / "definition"
    (definition / "tables").mkdir(parents=True, exist_ok=True)

    (sm / ".platform").write_text(
        tpl.platform_file(project_name, "SemanticModel"), encoding="utf-8"
    )

    model_content = model.model_tmdl.strip() or tpl.model_tmdl_header(project_name)
    (definition / "model.tmdl").write_text(model_content, encoding="utf-8")
    if model.relationships_tmdl.strip():
        (definition / "relationships.tmdl").write_text(
            model.relationships_tmdl, encoding="utf-8"
        )
    if model.expressions_tmdl.strip():
        (definition / "expressions.tmdl").write_text(
            model.expressions_tmdl, encoding="utf-8"
        )
    for fname, content in model.tables.items():
        safe = fname if fname.endswith(".tmdl") else f"{fname}.tmdl"
        (definition / "tables" / safe).write_text(content, encoding="utf-8")


def _write_report(project_name: str, report: ReportArtifacts, root: Path) -> None:
    rep = root / f"{project_name}.Report"
    definition = rep / "definition"
    pages_dir = definition / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    (rep / ".platform").write_text(
        tpl.platform_file(project_name, "Report"), encoding="utf-8"
    )
    (rep / "definition.pbir").write_text(
        tpl.definition_pbir(project_name), encoding="utf-8"
    )

    import json

    report_json = report.report_json or tpl.default_report_json()
    (definition / "report.json").write_text(
        json.dumps(report_json, indent=2), encoding="utf-8"
    )

    page_ids: list[str] = []
    for ordinal, page in enumerate(report.pages):
        page_id = page.get("page_id") or f"ReportSection{uuid.uuid4().hex[:12]}"
        page_ids.append(page_id)
        page_folder = pages_dir / page_id
        (page_folder / "visuals").mkdir(parents=True, exist_ok=True)

        page_json = page.get("page_json") or tpl.default_page_json(
            page_id, f"Page {ordinal + 1}", ordinal
        )
        page_json.setdefault("name", page_id)
        (page_folder / "page.json").write_text(
            json.dumps(page_json, indent=2), encoding="utf-8"
        )

        for visual in page.get("visuals", []):
            vid = visual.get("visual_id") or str(uuid.uuid4())
            vjson = visual.get("visual_json", {})
            vjson.setdefault("name", vid)
            vfolder = page_folder / "visuals" / vid
            vfolder.mkdir(parents=True, exist_ok=True)
            (vfolder / "visual.json").write_text(
                json.dumps(vjson, indent=2), encoding="utf-8"
            )

    (pages_dir / "pages.json").write_text(
        json.dumps(tpl.pages_order(page_ids), indent=2), encoding="utf-8"
    )


def zip_pbip(project_root: Path, project_name: str) -> Path:
    """Zip the .pbip project (folder + entry file) for download.

    The zip layout matches Power BI Desktop's native format: the .pbip entry file,
    .SemanticModel/, and .Report/ all sit at the top level — no intermediate
    {project_name}/ wrapper folder.
    """
    parent = project_root.parent
    archive_base = parent / f"{project_name}"
    staging = parent / f"_stage_{project_name}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    # Copy .SemanticModel/ and .Report/ directly into staging so they are siblings
    # of the .pbip file (matching the layout Power BI Desktop expects).
    for item in project_root.iterdir():
        dst = staging / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)
    entry = parent / f"{project_name}.pbip"
    if entry.exists():
        shutil.copy(entry, staging / f"{project_name}.pbip")
    zip_path = shutil.make_archive(str(archive_base), "zip", staging)
    shutil.rmtree(staging, ignore_errors=True)
    return Path(zip_path)
