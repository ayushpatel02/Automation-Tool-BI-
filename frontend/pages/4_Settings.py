"""Settings page: manage your own LLM provider API keys (stored encrypted on the server)."""

from __future__ import annotations

import streamlit as st

from api_client import ApiClient, ApiError
from components.auth import ensure_authenticated

client: ApiClient = ensure_authenticated()
st.title("⚙ Settings · API keys")

st.markdown(
    """
Add your own API key for each AI provider you want to use. Keys are encrypted per-user on
the server and are **never** shown back to you or sent to the browser. If you don't set a
key here, the tool falls back to a server-wide key (if the administrator configured one).
"""
)

try:
    statuses = client.list_api_keys()
except ApiError as exc:
    st.error(str(exc))
    st.stop()

_LABELS = {"google": "Google (Gemini)", "anthropic": "Anthropic (Claude)", "openai": "OpenAI (GPT)"}

for status in statuses:
    provider = status["provider"]
    label = _LABELS.get(provider, provider.title())
    configured = status["configured"]

    st.divider()
    badge = "✅ configured" if configured else "— not set"
    st.subheader(f"{label}  ·  {badge}")

    col_in, col_save, col_clear = st.columns([4, 1, 1])
    new_key = col_in.text_input(
        f"{label} API key",
        type="password",
        key=f"key_{provider}",
        placeholder="Paste key to set or replace",
        label_visibility="collapsed",
    )
    if col_save.button("Save", key=f"save_{provider}", disabled=not new_key.strip()):
        try:
            client.set_api_key(provider, new_key.strip())
            st.success(f"{label} key saved.")
            st.rerun()
        except ApiError as exc:
            st.error(str(exc))
    if col_clear.button("Clear", key=f"clear_{provider}", disabled=not configured):
        try:
            client.delete_api_key(provider)
            st.info(f"{label} key removed.")
            st.rerun()
        except ApiError as exc:
            st.error(str(exc))
