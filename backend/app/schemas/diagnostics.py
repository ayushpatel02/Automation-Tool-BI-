"""Schemas for the error-diagnosis helper."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DiagnoseRequest(BaseModel):
    model_id: str
    error_text: str = Field(min_length=1, max_length=8000)
    # Optional extra context (what the user was doing, the original report request, etc.).
    context: str | None = Field(default=None, max_length=4000)


class DiagnoseResponse(BaseModel):
    answer: str
    model_id: str
