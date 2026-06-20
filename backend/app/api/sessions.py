"""Generation session endpoints: create, stream progress (SSE), status, download, refine."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.connectors import load_connector_config
from app.api.deps import get_current_user
from app.connectors import get_connector
from app.db import get_db
from app.models import GenerationSession, User
from app.profiler import merge_profiles, profile_schema
from app.schemas.connector import ConnectorConfig
from app.schemas.generation import (
    GenerateRequest,
    RefineRequest,
    ReportArtifacts,
    SessionResponse,
)
from app.services import events
from app.services.generation_service import (
    revert_last,
    run_generation_task,
    run_refine_task,
    run_self_test_task,
)
from app.services.keys import resolve_api_key
from app.services.preview import build_preview

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _to_response(s: GenerationSession) -> SessionResponse:
    return SessionResponse(
        id=s.id,
        status=s.status,
        model_id=s.model_id,
        original_request=s.original_request,
        project_name=(s.current_artifacts or {}).get("project_name"),
        error_message=s.error_message,
        has_download=bool(s.download_path),
    )


@router.post("", response_model=SessionResponse, status_code=202)
async def create_session(
    body: GenerateRequest,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    # Resolve one or more connector configs (stored credentials and/or inline configs).
    configs: list[ConnectorConfig] = []
    for cid in body.credential_ids or ([body.credential_id] if body.credential_id else []):
        configs.append(await load_connector_config(cid, user, db))
    for c in body.connectors or ([body.connector] if body.connector else []):
        configs.append(ConnectorConfig(**c))

    if not configs:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one data source (credential_id(s) or connector(s))",
        )

    # Profile up-front so the request fails fast on a bad connection.
    profiles = []
    for config in configs:
        connector = get_connector(config)
        test = await connector.test_connection()
        if not test.ok:
            raise HTTPException(
                status_code=400,
                detail=f"Connection failed for '{config.name}': {test.message}",
            )
        profiles.append(await profile_schema(connector))

    profile = merge_profiles(profiles)
    api_key = resolve_api_key(user, body.model_id)

    session = GenerationSession(
        user_id=user.id,
        connector_id=body.credential_ids[0] if body.credential_ids else body.credential_id,
        status="pending",
        model_id=body.model_id,
        original_request=body.request,
        schema_profile=json.loads(profile.model_dump_json()),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    background.add_task(
        run_generation_task,
        session.id,
        api_key,
        profile,
        body.project_name,
    )
    return _to_response(session)


@router.get("/{session_id}/events")
async def stream_events(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _owned_session(session_id, user, db)

    async def event_generator():
        async for event in events.subscribe(session_id):
            yield {"event": "progress", "data": json.dumps(event)}
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    return _to_response(await _owned_session(session_id, user, db))


@router.get("/{session_id}/validation")
async def get_validation(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    session = await _owned_session(session_id, user, db)
    return session.last_validation or {}


@router.get("/{session_id}/download")
async def download(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    session = await _owned_session(session_id, user, db)
    if not session.download_path or not Path(session.download_path).exists():
        raise HTTPException(status_code=404, detail="No artifact available yet")
    return FileResponse(
        session.download_path,
        media_type="application/zip",
        filename=Path(session.download_path).name,
    )


@router.post("/{session_id}/refine", response_model=SessionResponse, status_code=202)
async def refine(
    session_id: str,
    body: RefineRequest,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    session = await _owned_session(session_id, user, db)
    if not session.current_artifacts:
        raise HTTPException(status_code=400, detail="Nothing to refine yet")
    api_key = resolve_api_key(user, session.model_id)
    project_name = session.current_artifacts.get("project_name", "GeneratedReport")
    background.add_task(
        run_refine_task, session.id, api_key, body.message, project_name
    )
    return _to_response(session)


@router.post("/{session_id}/selftest", response_model=SessionResponse, status_code=202)
async def self_test(
    session_id: str,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Re-run the self-test (deterministic + TE2 + LLM review) and auto-repair the report.

    Streams progress on the session's event channel; poll GET /validation for the report.
    """
    session = await _owned_session(session_id, user, db)
    if not session.current_artifacts:
        raise HTTPException(status_code=400, detail="Nothing to test yet")
    # A key enables the TE2/LLM-review layers + LLM repair; without one we still run the
    # deterministic linter + structural tree checks.
    try:
        api_key = resolve_api_key(user, session.model_id)
    except Exception:  # noqa: BLE001 — degrade gracefully to deterministic-only testing
        api_key = None
    project_name = session.current_artifacts.get("project_name", "GeneratedReport")
    background.add_task(run_self_test_task, session.id, api_key, project_name)
    return _to_response(session)


@router.get("/{session_id}/history")
async def history(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    session = await _owned_session(session_id, user, db)
    return {"count": len(session.artifact_history or [])}


@router.post("/{session_id}/revert", response_model=SessionResponse)
async def revert(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    session = await _owned_session(session_id, user, db)
    if not await revert_last(db, session):
        raise HTTPException(status_code=400, detail="No previous version to revert to")
    return _to_response(session)


@router.get("/{session_id}/preview")
async def preview(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    session = await _owned_session(session_id, user, db)
    if not session.current_artifacts:
        raise HTTPException(status_code=404, detail="No report to preview yet")
    report = ReportArtifacts(**session.current_artifacts["report"])
    return build_preview(report)


async def _owned_session(
    session_id: str, user: User, db: AsyncSession
) -> GenerationSession:
    session = (
        await db.execute(
            select(GenerationSession).where(GenerationSession.id == session_id)
        )
    ).scalar_one_or_none()
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return session
