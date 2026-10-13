"""Resolve the API key to use for a given model/provider for a given user.

Resolution order: the user's own stored key for the provider, then the server's default key
from settings (useful for a single-tenant self-hosted deployment).
"""

from __future__ import annotations

import json

from app.config import get_settings
from app.llm.router import model_config
from app.models import User
from app.security import decrypt_for_user

settings = get_settings()


def resolve_api_key(user: User, model_id: str) -> str | None:
    provider = model_config(model_id)["provider"]

    if user.encrypted_api_keys:
        try:
            keys = json.loads(decrypt_for_user(user.id, user.encrypted_api_keys.encode()))
            if keys.get(provider):
                return keys[provider]
        except Exception:  # noqa: BLE001 — fall through to server default
            pass

    return settings.default_provider_keys.get(provider)
