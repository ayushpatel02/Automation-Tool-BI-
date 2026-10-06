"""User-managed per-provider LLM API keys, stored encrypted on the user row.

Keys are held as a Fernet-encrypted JSON blob ({provider: key}) on ``User.encrypted_api_keys``
and consumed by ``app.services.keys.resolve_api_key``. The plaintext key is never returned to
the client — endpoints only report whether a provider is configured.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.llm import available_models
from app.models import User
from app.schemas.auth import ApiKeyRequest, ApiKeyStatus
from app.security import decrypt_for_user, encrypt_for_user

router = APIRouter(prefix="/auth/api-keys", tags=["auth"])


def _known_providers() -> list[str]:
    """Distinct providers in the model registry, preserving first-seen order."""
    seen: list[str] = []
    for m in available_models():
        provider = m.get("provider")
        if provider and provider not in seen:
            seen.append(provider)
    return seen


def _load_keys(user: User) -> dict[str, str]:
    if not user.encrypted_api_keys:
        return {}
    try:
        return json.loads(decrypt_for_user(user.id, user.encrypted_api_keys.encode()))
    except Exception:  # noqa: BLE001 — corrupt/rotated blob: treat as no keys
        return {}


def _store_keys(user: User, keys: dict[str, str]) -> None:
    user.encrypted_api_keys = (
        encrypt_for_user(user.id, json.dumps(keys)).decode() if keys else None
    )


def _status(keys: dict[str, str]) -> list[ApiKeyStatus]:
    return [
        ApiKeyStatus(provider=p, configured=bool(keys.get(p))) for p in _known_providers()
    ]


@router.get("", response_model=list[ApiKeyStatus])
async def list_api_keys(user: User = Depends(get_current_user)) -> list[ApiKeyStatus]:
    return _status(_load_keys(user))


@router.put("", response_model=list[ApiKeyStatus])
async def set_api_key(
    body: ApiKeyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyStatus]:
    if body.provider not in _known_providers():
        raise HTTPException(status_code=400, detail=f"Unknown provider: {body.provider}")
    keys = _load_keys(user)
    if body.api_key.strip():
        keys[body.provider] = body.api_key.strip()
    else:
        keys.pop(body.provider, None)
    _store_keys(user, keys)
    await db.commit()
    return _status(keys)


@router.delete("/{provider}", response_model=list[ApiKeyStatus])
async def delete_api_key(
    provider: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyStatus]:
    keys = _load_keys(user)
    keys.pop(provider, None)
    _store_keys(user, keys)
    await db.commit()
    return _status(keys)
