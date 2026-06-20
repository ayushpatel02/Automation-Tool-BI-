"""Apply a natural-language edit to an existing generated report.

v1 strategy (per the plan): targeted regeneration. We classify the edit, then regenerate the
affected layer(s). If the edit touches the semantic model, we regenerate it (and the report,
since its references may change). If it only touches the report, we regenerate just the
report against the unchanged model. Both paths re-run the validate-and-retry loop and store
an artifact snapshot for undo.

This is intentionally coarser than surgical per-file patching (a v2 optimization), but it is
robust: every result is fully re-validated before it is returned.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from app.generation import report as report_gen
from app.generation.pipeline import GenerationOutcome, _regen_report, _retry_loop
from app.llm.router import LLMRouter
from app.refinement.classifier import classify_edit
from app.schemas.connector import SchemaProfile
from app.schemas.generation import ReportArtifacts, SemanticModelArtifacts
from app.validation import validate_report

ProgressCb = Callable[[dict], Awaitable[None]]


async def _noop(_: dict) -> None:
    pass


def _edit_request(original_request: str, edit: str, history: list[dict]) -> str:
    """Compose a request string that carries enough context for a faithful edit."""
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
    return (
        f"Original report request: {original_request}\n"
        f"Recent conversation:\n{convo}\n\n"
        f"Apply this change: {edit}"
    )


async def refine_report(
    *,
    llm: LLMRouter,
    edit: str,
    model_art: SemanticModelArtifacts,
    report_art: ReportArtifacts,
    profile: SchemaProfile,
    original_request: str,
    conversation_history: list[dict],
    project_name: str,
    output_dir: Path,
    progress: ProgressCb | None = None,
) -> GenerationOutcome:
    emit = progress or _noop
    outcome = GenerationOutcome(success=False, semantic_model=model_art, report=report_art)

    classification = await classify_edit(llm, edit)
    scopes = classification["scope"]
    await emit({"stage": "classify", "status": "ok", "scope": scopes})

    composed = _edit_request(original_request, edit, conversation_history)
    touches_model = any(s.startswith("TMDL_") for s in scopes) or "FULL_REGENERATION" in scopes

    # If the edit touches the model, regenerate it first (then the report must follow).
    if touches_model:
        from app.generation import semantic_model as model_gen

        await emit({"stage": "gen_model", "status": "start"})
        new_model, model_raw, base_msgs = await model_gen.generate_semantic_model(
            llm, profile, composed
        )
        from app.validation import validate_semantic_model

        new_model, model_val, _ = await _retry_loop(
            emit,
            stage="model",
            artifacts=new_model,
            raw=model_raw,
            base_messages=base_msgs,
            validate=lambda art: validate_semantic_model(art),
            regenerate=lambda msgs: _regen_model_local(llm, msgs),
            build_repair=lambda raw, errs: model_gen.build_repair_messages(base_msgs, raw, errs),
        )
        outcome.model_validation = model_val
        if not model_val.valid:
            outcome.error = "Refined semantic model failed validation"
            return outcome
        model_art = new_model
        outcome.semantic_model = model_art

    # Always regenerate the report (its field references may depend on the model).
    await emit({"stage": "gen_report", "status": "start"})
    new_report, report_raw, base_msgs_r = await report_gen.generate_report(
        llm, model_art, composed
    )
    new_report, report_val, _ = await _retry_loop(
        emit,
        stage="report",
        artifacts=new_report,
        raw=report_raw,
        base_messages=base_msgs_r,
        validate=lambda art: _validate_report(art, model_art),
        regenerate=lambda msgs: _regen_report(llm, msgs),
        build_repair=lambda raw, errs: report_gen.build_repair_messages(base_msgs_r, raw, errs),
    )
    outcome.report = new_report
    outcome.report_validation = report_val

    from app.assembler import assemble_pbip, zip_pbip
    from app.generation.semantic_model import patch_file_sources

    patched_model, data_files = patch_file_sources(model_art, profile)
    project_root = assemble_pbip(project_name, patched_model, new_report, output_dir)
    outcome.project_root = project_root
    outcome.zip_path = zip_pbip(project_root, project_name, data_files=data_files or None)
    outcome.success = report_val.valid
    await emit({"stage": "assemble", "status": "ok", "success": outcome.success})
    return outcome


async def _validate_report(report: ReportArtifacts, model: SemanticModelArtifacts):
    return validate_report(report, model)


async def _regen_model_local(llm: LLMRouter, messages: list[dict]):
    from app.generation import semantic_model as model_gen

    raw = await llm.complete_json(messages)
    return model_gen.parse_artifacts(raw), raw
