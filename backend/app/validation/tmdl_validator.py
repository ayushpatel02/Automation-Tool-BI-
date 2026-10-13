"""TMDL validation.

There is no pure-Python TMDL parser/validator, so authoritative validation shells out to
the Tabular Editor 2 CLI (path from TE2_CLI_PATH). When TE2 is unavailable (e.g. local dev
without the binary), we degrade to best-effort *structural* checks so the pipeline still
produces feedback for the retry loop — this is explicitly a weaker check and logs a warning.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path

from app.config import get_settings
from app.schemas.generation import (
    SemanticModelArtifacts,
    ValidationError,
    ValidationResult,
)

logger = logging.getLogger(__name__)
settings = get_settings()


async def validate_semantic_model(model: SemanticModelArtifacts) -> ValidationResult:
    te2 = settings.te2_cli_path
    if te2 and Path(te2).exists():
        return await _validate_with_te2(model, te2)
    logger.warning(
        "TE2_CLI_PATH not set or missing; falling back to structural TMDL checks only."
    )
    return _structural_checks(model)


def te2_available() -> bool:
    """True when an authoritative Tabular Editor 2 CLI is configured and present."""
    te2 = settings.te2_cli_path
    return bool(te2 and Path(te2).exists())


async def run_te2_if_available(model: SemanticModelArtifacts) -> ValidationResult | None:
    """Run the authoritative TE2 compile when configured; otherwise return None.

    Lets the self-test add a TE2 layer without duplicating the structural fallback that
    the deterministic linter already covers.
    """
    te2 = settings.te2_cli_path
    if not (te2 and Path(te2).exists()):
        return None
    return await _validate_with_te2(model, te2)


# --- TE2 CLI path ----------------------------------------------------------

async def _validate_with_te2(model: SemanticModelArtifacts, te2_path: str) -> ValidationResult:
    tmp = Path(tempfile.mkdtemp(prefix="tmdl_"))
    try:
        _write_tmdl_tree(model, tmp)
        # NOTE: Exact TE2 CLI flags must be verified against current Tabular Editor 2 docs.
        # The intent: load the TMDL folder, run Best Practice Analyzer, exit non-zero on error.
        proc = await asyncio.create_subprocess_exec(
            te2_path,
            str(tmp),
            "-A",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError:
            proc.kill()
            return ValidationResult(
                valid=False,
                errors=[ValidationError(file="model", message="TE2 validation timed out")],
            )
        return _parse_te2_output(proc.returncode or 0, stdout.decode(), stderr.decode())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _parse_te2_output(returncode: int, stdout: str, stderr: str) -> ValidationResult:
    if returncode == 0:
        return ValidationResult.ok()
    errors: list[ValidationError] = []
    for line in (stdout + "\n" + stderr).splitlines():
        line = line.strip()
        if not line:
            continue
        if re.search(r"\b(error|invalid|fail)", line, re.IGNORECASE):
            errors.append(ValidationError(file="model", message=line))
    if not errors:
        errors.append(
            ValidationError(file="model", message=f"TE2 exited with code {returncode}")
        )
    return ValidationResult(valid=False, errors=errors)


# --- Structural fallback ---------------------------------------------------

def _structural_checks(model: SemanticModelArtifacts) -> ValidationResult:
    """Deep deterministic TMDL checks (the pre-flight linter), mapped to a ValidationResult.

    This is what the per-stage retry loop validates against when TE2 is unavailable, so it
    must catch the Power-BI-open-time format errors (tabs, mode casing, booleans, M types)
    rather than only the coarse structural ones.
    """
    from app.validation.preflight import lint_model

    findings = lint_model(model)
    errors = [
        ValidationError(file=f.file, message=f.message)
        for f in findings
        if f.severity == "error"
    ]
    warnings = [
        ValidationError(file=f.file, message=f.message, severity="warning")
        for f in findings
        if f.severity == "warning"
    ]
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def _write_tmdl_tree(model: SemanticModelArtifacts, root: Path) -> None:
    definition = root / "definition"
    (definition / "tables").mkdir(parents=True, exist_ok=True)
    (definition / "model.tmdl").write_text(model.model_tmdl, encoding="utf-8")
    if model.relationships_tmdl:
        (definition / "relationships.tmdl").write_text(
            model.relationships_tmdl, encoding="utf-8"
        )
    if model.expressions_tmdl:
        (definition / "expressions.tmdl").write_text(
            model.expressions_tmdl, encoding="utf-8"
        )
    for fname, content in model.tables.items():
        (definition / "tables" / fname).write_text(content, encoding="utf-8")
