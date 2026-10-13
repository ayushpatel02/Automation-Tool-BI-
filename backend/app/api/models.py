"""Endpoint exposing the selectable LLM model registry to the frontend."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.llm import available_models
from app.models import User

router = APIRouter(prefix="/models", tags=["models"])


@router.get("")
async def list_models(user: User = Depends(get_current_user)) -> list[dict]:
    return available_models()
