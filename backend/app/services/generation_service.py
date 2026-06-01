"""Run generation / refinement as background tasks, persisting results to the session row."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.generation import run_generation
from app.llm.router import LLMRouter
from app.models import GenerationSession
from app.schemas.connector import ConnectorType, SchemaProfile
from app.schemas.generation import ReportArtifacts, SemanticModelArtifacts
from app.services import events

logger = logging.getLogger(__name__)
settings = get_settings()
_MAX_HISTORY = 5


async def _load_session(db, session_id: str) -> GenerationSession | None:
    return (
        await db.execute(
            select(GenerationSession).where(GenerationSession.id == session_id)
        )
    ).scalar_one_or_none()


async def run_generation_task(
    session_id: str,
    api_key: str | None,
    profile: SchemaProfile,
    connector_type: ConnectorType,
    project_name: str,
) -> None:
    cb = events.make_progress_cb(session_id)
    async with SessionLocal() as db:
        session = await _load_session(db, session_id)
        if session is None:
            await events.close(session_id)
            return
        session.status = "running"
        await db.commit()

        try:
            llm = LLMRouter(session.model_id, api_key)
            output_dir = settings.generated_dir / session_id
            output_dir.mkdir(parents=True, exist_ok=True)
            outcome = await run_generation(
                llm=llm,
                profile=profile,
                user_request=session.original_request,
                connector_type=connector_type,
                project_name=project_name,
                output_dir=output_dir,
                progress=cb,
            )
            await _persist_outcome(db, session, outcome)
        except Exception as exc:  # noqa: BLE001 — record failure, never crash the worker
            logger.exception("Generation failed for session %s", session_id)
            session.status = "failed"
            session.error_message = str(exc)
            await db.commit()
            await cb({"stage": "error", "status": "error", "message": str(exc)})
        finally:
            await events.close(session_id)


async def run_refine_task(
    session_id: str, api_key: str | None, edit: str, project_name: str
) -> None:
    from app.refinement import refine_report

    cb = events.make_progress_cb(session_id)
    async with SessionLocal() as db:
        session = await _load_session(db, session_id)
        if session is None or not session.current_artifacts:
            await events.close(session_id)
            return
        session.status = "refining"
        history = list(session.conversation_history or [])
        history.append({"role": "user", "content": edit})
        session.conversation_history = history
        await db.commit()

        try:
            artifacts = session.current_artifacts
            model_art = SemanticModelArtifacts(**artifacts["semantic_model"])
            report_art = ReportArtifacts(**artifacts["report"])
            profile = SchemaProfile(**session.schema_profile)
            llm = LLMRouter(session.model_id, api_key)
            output_dir = settings.generated_dir / session_id
            output_dir.mkdir(parents=True, exist_ok=True)

            outcome = await refine_report(
                llm=llm,
                edit=edit,
                model_art=model_art,
                report_art=report_art,
                profile=profile,
                original_request=session.original_request,
                conversation_history=history,
                project_name=project_name,
                output_dir=output_dir,
                progress=cb,
            )
            _snapshot(session)
            await _persist_outcome(db, session, outcome, status_done="complete")
            history.append(
                {"role": "assistant", "content": "Applied edit and re-validated."}
            )
            session.conversation_history = history
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Refinement failed for session %s", session_id)
            session.status = "complete"  # keep prior artifacts usable
            session.error_message = str(exc)
            await db.commit()
            await cb({"stage": "error", "status": "error", "message": str(exc)})
        finally:
            await events.close(session_id)


def _snapshot(session: GenerationSession) -> None:
    if not session.current_artifacts:
        return
    history = list(session.artifact_history or [])
    history.append(session.current_artifacts)
    session.artifact_history = history[-_MAX_HISTORY:]


async def _persist_outcome(db, session, outcome, status_done: str = "complete") -> None:
    if outcome.semantic_model and outcome.report:
        session.current_artifacts = {
            "semantic_model": outcome.semantic_model.model_dump(),
            "report": outcome.report.model_dump(),
        }
    if outcome.zip_path:
        session.download_path = str(outcome.zip_path)
    session.last_validation = {
        "model": outcome.model_validation.model_dump() if outcome.model_validation else None,
        "report": outcome.report_validation.model_dump()
        if outcome.report_validation
        else None,
        "attempts": outcome.attempts,
    }
    session.status = status_done if outcome.success else "failed"
    if not outcome.success and outcome.error:
        session.error_message = outcome.error
    await db.commit()
