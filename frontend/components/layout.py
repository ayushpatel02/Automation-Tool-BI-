"""Shared page chrome: a consistent sidebar across every page."""

from __future__ import annotations

import streamlit as st
from api_client import ApiClient

from components.sources import get_sources

_TYPE_ICON = {
    "postgresql": "🐘", "mysql": "🐬", "sqlserver": "🗄", "snowflake": "❄",
    "bigquery": "🔎", "databricks": "🧱", "csv": "📄", "excel": "📊",
}


def render_sidebar(client: ApiClient, *, active: str = "") -> None:
    """Render the shared sidebar: account, connected sources, and a log-out button.

    *active* is the current page's short name, used only to omit redundant hints.
    """
    with st.sidebar:
        email = st.session_state.get("email", "your account")
        st.markdown(f"**👤 {email}**")

        sources = get_sources(client)
        st.markdown("---")
        if sources:
            st.caption(f"Connected sources ({len(sources)})")
            for s in sources:
                icon = _TYPE_ICON.get(s.get("type", ""), "•")
                st.markdown(f"{icon} {s['name']}")
        else:
            st.caption("No data sources yet")
            if active != "connect":
                st.caption("Add one on the **Connect** page.")

        st.markdown("---")
        if st.button("Log out", use_container_width=True):
            for key in list(st.session_state.keys()):
                st.session_state.pop(key, None)
            st.rerun()
