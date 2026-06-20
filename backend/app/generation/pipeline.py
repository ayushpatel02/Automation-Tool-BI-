"""Generation pipeline: profile -> TMDL (validate+retry) -> PBIR (validate+retry) -> assemble.

Emits progress events through an async callback so the API can stream them over SSE. The
retry loop re-prompts the model with the specific validation errors (a 'repair' prompt),
which is what pushes past the ~85-90% first-try success rate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings
from app.generation import report as report_gen
from app.generation import semantic_model as model_gen
from app.llm.router import LLMError, LLMRouter
from app.schemas.connector import SchemaProfile
from app.schemas.generation import (
    ReportArtifacts,
    SelfTestReport,
    SemanticModelArtifacts,
    ValidationResult,
)
from app.validation import validate_report, validate_semantic_model

settings = get_settings()

ProgressCb = Callable[[dict], Awaitable[None]]


@dataclass
class GenerationOutcome:
    success: bool
    semantic_model: SemanticModelArtifacts | None = None
    report: ReportArtifacts | None = None
    model_validation: ValidationResult | None = None
    report_validation: ValidationResult | None = None
    self_test: SelfTestReport | None = None
    project_root: Path | None = None
    zip_path: Path | None = None
    attempts: dict = field(default_factory=dict)
    error: str | None = None


async def _noop(_: dict) -> None:
    pass


async def run_generation(
    *,
    llm: LLMRouter,
    profile: SchemaProfile,
    user_request: str,
    project_name: str,
    output_dir: Path,
    progress: ProgressCb | None = None,
    enable_self_test: bool = True,
    run_llm_review: bool = True,
) -> GenerationOutcome:
    emit = progress or _noop
    outcome = GenerationOutcome(success=False)

    # --- Stage 1: semantic model -----------------------------------------
    await emit({"stage": "gen_model", "status": "start"})
    try:
        model_art, model_raw, base_msgs = await model_gen.generate_semantic_model(
            llm, profile, user_request
        )
    except LLMError as exc:
        outcome.error = f"Model generation failed: {exc}"
        await emit({"stage": "gen_model", "status": "error", "message": str(exc)})
        return outcome

    model_art, model_val, model_attempts = await _retry_loop(
        emit,
        stage="model",
        artifacts=model_art,
        raw=model_raw,
        base_messages=base_msgs,
        validate=lambda art: validate_semantic_model(art),
        regenerate=lambda msgs: _regen_model(llm, msgs),
        build_repair=lambda raw, errs: model_gen.build_repair_messages(base_msgs, raw, errs),
    )
    outcome.semantic_model = model_art
    outcome.model_validation = model_val
    outcome.attempts["model"] = model_attempts
    if not model_val.valid:
        outcome.error = "Semantic model failed validation after retries"
        await emit({"stage": "validate_model", "status": "failed"})
        # Still continue to report generation on a best-effort basis? No — model is the
        # foundation; abort to avoid cascading invalid references.
        return outcome
    await emit({"stage": "validate_model", "status": "ok"})

    # --- Stage 2: report --------------------------------------------------
    await emit({"stage": "gen_report", "status": "start"})
    try:
        report_art, report_raw, base_msgs_r = await report_gen.generate_report(
            llm, model_art, user_request
        )
    except LLMError as exc:
        outcome.error = f"Report generation failed: {exc}"
        await emit({"stage": "gen_report", "status": "error", "message": str(exc)})
        return outcome

    report_art, report_val, report_attempts = await _retry_loop(
        emit,
        stage="report",
        artifacts=report_art,
        raw=report_raw,
        base_messages=base_msgs_r,
        validate=lambda art: _validate_report_async(art, model_art),
        regenerate=lambda msgs: _regen_report(llm, msgs),
        build_repair=lambda raw, errs: report_gen.build_repair_messages(base_msgs_r, raw, errs),
    )
    outcome.report = report_art
    outcome.report_validation = report_val
    outcome.attempts["report"] = report_attempts
    await emit(
        {"stage": "validate_report", "status": "ok" if report_val.valid else "failed"}
    )

    # --- Self-test gate: test the report by itself, auto-repair, then re-test ---
    if enable_self_test:
        await emit({"stage": "self_test", "status": "start"})
        from app.validation import self_test_and_repair

        st = await self_test_and_repair(
            model=model_art,
            report=report_art,
            profile=profile,
            request=user_request,
            project_name=project_name,
            output_dir=output_dir,
            llm=llm,
            run_llm_review=run_llm_review,
            progress=progress,
        )
        outcome.semantic_model = st.model
        outcome.report = st.report
        outcome.project_root = st.project_root
        outcome.zip_path = st.zip_path
        outcome.self_test = st.report_card
        outcome.success = report_val.valid and st.report_card.passed
        await emit({
            "stage": "self_test",
            "status": "done",
            "passed": st.report_card.passed,
            "success": outcome.success,
            "remaining": [f.message for f in st.report_card.errors][:10],
        })
        return outcome

    # --- Assemble (self-test disabled): still give the user something ---
    await emit({"stage": "assemble", "status": "start"})
    from app.assembler import assemble_pbip, zip_pbip

    project_root = assemble_pbip(project_name, model_art, report_art, output_dir)
    zip_path = zip_pbip(project_root, project_name)
    outcome.project_root = project_root
    outcome.zip_path = zip_path
    outcome.success = report_val.valid
    await emit({"stage": "assemble", "status": "ok", "success": outcome.success})
    return outcome


# --- Helpers ---------------------------------------------------------------

async def _validate_report_async(report: ReportArtifacts, model: SemanticModelArtifacts):
    return validate_report(report, model)


async def _regen_model(llm: LLMRouter, messages: list[dict]):
    raw = await llm.complete_json(messages)
    return model_gen.parse_artifacts(raw), raw


async def _regen_report(llm: LLMRouter, messages: list[dict]):
    # JSON mode, not structured output — see report.generate_report for why.
    raw = await llm.complete_json(messages)
    return report_gen.parse_artifacts(raw), raw


async def _retry_loop(
    emit: ProgressCb,
    *,
    stage: str,
    artifacts,
    raw: dict,
    base_messages: list[dict],
    validate: Callable[[object], Awaitable[ValidationResult]],
    regenerate: Callable[[list[dict]], Awaitable[tuple]],
    build_repair: Callable[[dict, list[str]], list[dict]],
):
    """Validate; on failure, re-prompt with the errors up to MAX_RETRIES times."""
    validation = await validate(artifacts)
    attempt = 1
    while not validation.valid and attempt <= settings.max_retries:
        error_msgs = [e.message for e in validation.errors][:15]
        await emit(
            {
                "stage": f"validate_{stage}",
                "status": "retry",
                "attempt": attempt,
                "errors": error_msgs,
            }
        )
        repair_messages = build_repair(raw, error_msgs)
        try:
            artifacts, raw = await regenerate(repair_messages)
        except LLMError as exc:
            await emit(
                {"stage": f"gen_{stage}", "status": "error", "message": str(exc)}
            )
            break
        validation = await validate(artifacts)
        attempt += 1

    return artifacts, validation, attempt
