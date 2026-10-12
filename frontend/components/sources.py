"""Shared data-source helpers.

The backend already persists every connector a user saves (encrypted, including the
uploaded file's server path), so saved sources — and their uploaded files — survive an
app restart or re-login. These helpers make the backend the source of truth for the
connector list instead of Streamlit's in-memory session state, so the user never has to
re-add or re-upload a source they already connected.
"""

from __future__ import annotations

import streamlit as st
from api_client import ApiClient, ApiError

_CACHE_KEY = "connectors"
_LOADED_KEY = "_sources_loaded"


def _map(row: dict) -> dict:
    """Backend connector row -> the shape the pages use."""
    return {
        "name": row.get("name", "unnamed"),
        "type": row.get("connector_type", ""),
        "credential_id": row.get("id"),
    }


def refresh_sources(client: ApiClient) -> list[dict]:
    """Fetch the user's saved connectors from the backend and cache them."""
    try:
        rows = client.list_connectors() or []
    except ApiError:
        rows = st.session_state.get(_CACHE_KEY) or []  # keep last-known on a blip
        return rows
    sources = [_map(r) for r in rows]
    st.session_state[_CACHE_KEY] = sources
    st.session_state[_LOADED_KEY] = True
    return sources


def get_sources(client: ApiClient) -> list[dict]:
    """Return saved sources, loading them from the backend once per session."""
    if not st.session_state.get(_LOADED_KEY):
        return refresh_sources(client)
    return st.session_state.get(_CACHE_KEY) or []
