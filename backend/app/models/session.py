"""Generation session ORM model — holds artifacts + history for the refinement loop."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class GenerationSession(Base):
    __tablename__ = "generation_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    connector_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="pending")
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    original_request: Mapped[str] = mapped_column(Text, nullable=False)

    schema_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # All current TMDL + PBIR file contents (the live artifact set).
    current_artifacts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Past artifact snapshots for undo (capped in application logic).
    artifact_history: Mapped[list | None] = mapped_column(JSON, default=list)
    # Refinement chat history: list of {role, content}.
    conversation_history: Mapped[list | None] = mapped_column(JSON, default=list)
    # Validation summary / errors of the most recent generation.
    last_validation: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    download_path: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
