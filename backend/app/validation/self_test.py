"""Self-test: run a report through several test layers and auto-repair what fails.

Layers (each best-effort and independently skippable):
  1. deterministic — the pre-flight linter (always runs; fast, free).
  2. te2           — authoritative Tabular Editor 2 compile (only when TE2_CLI_PATH is set).
  3. llm_review    — a second LLM pass that critiques the report against the request
                     (only when an LLM is supplied).

``self_test_and_repair`` loops: run the layers, apply deterministic fixes for ``auto``
findings and re-prompt the model for ``llm`` findings, re-assemble, and re-test — up to
``max_attempts`` — then return the best artifacts plus the final report. This is what lets
the app "test the report by itself and then provide the output."
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.assembler import assemble_pbip, zip_pbip
from app.config import get_settings
from app.llm.router import LLMError, LLMRouter
from app.schemas.connector import SchemaProfile
from app.schemas.generation import (
    PreflightFinding,
    ReportArtifacts,
    SelfTestReport,
    SemanticModelArtifacts,
    TestLayer,
)
from app.validation.pbir_validator import _iter_field_refs
from app.validation.preflight import lint_model, preflight
from app.validation.tmdl_validator import run_te2_if_available

# NOTE: app.generation.* is imported lazily inside the functions that need it. Importing it
# at module load creates a cycle (validation -> self_test -> generation -> pipeline ->
# validation), so the generation modules are pulled in on first use instead.

logger = logging.getLogger(__name__)
settings = get_settings()

ProgressCb = Callable[[dict], Awaitable[None]]

_PROMPTS = Path(__file__).parent.parent / "generation" / "prompts"
_VISUAL_CONTAINER_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/"
    "definition/visualContainer/2.0.0/schema.json"
)


@dataclass
class SelfTestResult:
    model: SemanticModelArtifacts
    report: ReportArtifacts
    project_root: Path
    zip_path: Path
    report_card: SelfTestReport


# --- Layer 3: LLM self-review ---------------------------------------------

def _report_summary(report: ReportArtifacts) -> str:
    lines: list[str] = []
    for page in report.pages:
        pj = page.get("page_json", {}) or {}
        name = pj.get("displayName") or pj.get("name") or page.get("page_id", "Page")
        lines.append(f"PAGE {name}")
        for v in page.get("visuals", []):
            vj = v.get("visual_json", {}) or {}
            vtype = vj.get("visual", {}).get("visualType", "visual")
            fields = sorted({f"{e}.{p}" for e, p in _iter_field_refs(vj)})
            lines.append(f"  - {vtype}: {', '.join(fields) if fields else '(no fields)'}")
    return "\n".join(lines)


async def review_artifacts(
    llm: LLMRouter,
    model: SemanticModelArtifacts,
    report: ReportArtifacts,
    request: str,
) -> list[PreflightFinding]:
    """LLM self-review: surface semantic/completeness issues static checks can't see."""
    from app.generation import report as report_gen

    system = (_PROMPTS / "self_review.txt").read_text(encoding="utf-8")
    user = (
        f"USER REQUEST:\n{request}\n\n"
        f"SEMANTIC MODEL:\n{report_gen.semantic_model_summary(model)}\n\n"
        f"REPORT:\n{_report_summary(report)}\n\n"
        "Review the report now."
    )
    raw = await llm.complete_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}]
    )
    findings: list[PreflightFinding] = []
    for issue in raw.get("issues", []) or []:
        if not isinstance(issue, dict) or not issue.get("message"):
            continue
        severity = "error" if issue.get("severity") == "error" else "warning"
        findings.append(
            PreflightFinding(
                category=str(issue.get("category") or "review.issue"),
                message=str(issue["message"]),
                severity=severity,
                file=str(issue.get("file") or ""),
                fix="llm",
                layer="llm_review",
            )
        )
    return findings


# --- Orchestration ---------------------------------------------------------

async def run_self_test(
    model: SemanticModelArtifacts,
    report: ReportArtifacts,
    *,
    project_root: Path | None = None,
    llm: LLMRouter | None = None,
    request: str = "",
    run_llm_review: bool = True,
) -> SelfTestReport:
    """Run every available test layer once and aggregate the findings (no repair)."""
    layers_run: list[TestLayer] = ["deterministic"]
    findings: list[PreflightFinding] = list(preflight(model, report, project_root))

    te2_result = await run_te2_if_available(model)
    if te2_result is not None:
        layers_run.append("te2")
        for err in te2_result.errors:
            findings.append(
                PreflightFinding(
                    category="te2.compile",
                    message=err.message,
                    file=err.file or "model",
                    fix="llm",
                    layer="te2",
                )
            )

    if llm is not None and run_llm_review:
        try:
            review = await review_artifacts(llm, model, report, request)
            layers_run.append("llm_review")
            findings += review
        except LLMError as exc:  # never let review crash the test
            logger.warning("LLM self-review skipped: %s", exc)
            findings.append(
                PreflightFinding(
                    category="llm_review.skipped",
                    message=f"LLM self-review could not run: {exc}",
                    severity="warning",
                    fix="manual",
                    layer="llm_review",
                )
            )

    return SelfTestReport.from_findings(findings, layers_run=layers_run)


def _apply_report_auto_fixes(report: ReportArtifacts) -> list[str]:
    """Inject any missing visual $schema URLs in place. Returns descriptions of fixes."""
    changed = 0
    for page in report.pages:
        for v in page.get("visuals", []):
            vj = v.get("visual_json")
            if isinstance(vj, dict) and not vj.get("$schema"):
                vj["$schema"] = _VISUAL_CONTAINER_SCHEMA
                changed += 1
    return [f"Injected $schema on {changed} visual(s)"] if changed else []


async def _llm_repair_model(
    llm: LLMRouter, profile: SchemaProfile, request: str,
    model: SemanticModelArtifacts, errors: list[str],
) -> SemanticModelArtifacts:
    from app.generation import semantic_model as model_gen

    base = model_gen.build_messages(profile, request)
    messages = model_gen.build_repair_messages(base, model.model_dump(), errors)
    raw = await llm.complete_json(messages)
    return model_gen.parse_artifacts(raw)


async def _llm_repair_report(
    llm: LLMRouter, model: SemanticModelArtifacts, request: str,
    report: ReportArtifacts, errors: list[str],
) -> ReportArtifacts:
    from app.generation import report as report_gen

    base = report_gen.build_messages(model, request)
    messages = report_gen.build_repair_messages(base, report.model_dump(), errors)
    schema = report_gen.PBIR_RESPONSE_SCHEMA if llm.config.get("supports_structured_output") else None
    raw = await llm.complete_json(messages, json_schema=schema)
    return report_gen.parse_artifacts(raw)


async def self_test_and_repair(
    *,
    model: SemanticModelArtifacts,
    report: ReportArtifacts,
    profile: SchemaProfile,
    request: str,
    project_name: str,
    output_dir: Path,
    llm: LLMRouter | None = None,
    run_llm_review: bool = True,
    max_attempts: int | None = None,
    progress: ProgressCb | None = None,
) -> SelfTestResult:
    """Test the report; auto-repair failures and re-test until clean or out of attempts."""
    from app.generation.semantic_model import sanitize_model

    max_attempts = max_attempts if max_attempts is not None else settings.max_retries
    auto_fixed: list[str] = []
    attempt = 0

    async def emit(payload: dict) -> None:
        if progress:
            await progress(payload)

    while True:
        attempt += 1
        # Deterministic auto-fixes first (cheap, no tokens).
        auto_cats = sorted({f.category for f in lint_model(model) if f.fix == "auto"})
        new_model = sanitize_model(model)
        if auto_cats and new_model.model_dump() != model.model_dump():
            auto_fixed.append("Normalized TMDL: " + ", ".join(auto_cats))
        model = new_model
        auto_fixed += _apply_report_auto_fixes(report)

        project_root = assemble_pbip(project_name, model, report, output_dir)
        card = await run_self_test(
            model, report, project_root=project_root, llm=llm,
            request=request, run_llm_review=run_llm_review,
        )
        await emit({
            "stage": "self_test", "status": "ok" if card.passed else "issues",
            "attempt": attempt, "errors": [f.message for f in card.errors][:10],
            "layers": card.layers_run,
        })

        if card.passed or attempt > max_attempts or llm is None:
            break

        # Re-prompt the model for the issues a normalizer cannot fix.
        llm_errors = [f for f in card.errors if f.fix == "llm"]
        if not llm_errors:
            break  # remaining errors are not LLM-repairable; stop looping.

        model_errs = [f.message for f in llm_errors if f.category.startswith(("tmdl", "te2"))]
        report_errs = [
            f.message for f in llm_errors
            if f.category.startswith(("pbir", "structure.dataset", "review"))
        ]
        await emit({"stage": "self_test", "status": "repairing", "attempt": attempt})
        try:
            if model_errs:
                model = await _llm_repair_model(llm, profile, request, model, model_errs)
            if report_errs:
                report = await _llm_repair_report(llm, model, request, report, report_errs)
        except LLMError as exc:
            logger.warning("Self-test repair attempt %s failed: %s", attempt, exc)
            await emit({"stage": "self_test", "status": "repair_error", "message": str(exc)})
            break

    # Final assembly + re-lint the tree so the report card reflects what we shipped.
    project_root = assemble_pbip(project_name, model, report, output_dir)
    final = await run_self_test(
        model, report, project_root=project_root, llm=llm,
        request=request, run_llm_review=run_llm_review,
    )
    final.attempts = attempt
    final.auto_fixed = auto_fixed
    zip_path = zip_pbip(project_root, project_name)
    return SelfTestResult(
        model=model, report=report, project_root=project_root,
        zip_path=zip_path, report_card=final,
    )


def quick_self_test(
    model: SemanticModelArtifacts,
    report: ReportArtifacts,
    project_root: Path | None = None,
) -> SelfTestReport:
    """Synchronous deterministic-only self-test (no TE2, no LLM) for fast checks/tests."""
    findings = preflight(model, report, project_root)
    return SelfTestReport.from_findings(findings, layers_run=["deterministic"])
