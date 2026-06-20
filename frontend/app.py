"""Streamlit entrypoint. Auth gate + simple navigation across the workflow pages."""

from __future__ import annotations

import streamlit as st

from components.auth import ensure_authenticated

st.set_page_config(page_title="AI Power BI Generator", page_icon="📊", layout="wide")

client = ensure_authenticated()

st.sidebar.success(f"Signed in as {st.session_state.get('email', 'user')}")
if st.sidebar.button("Log out"):
    for key in ("token", "email", "session_id", "connectors", "project_name"):
        st.session_state.pop(key, None)
    st.rerun()

st.title("📊 AI-Powered Power BI Report Generator")
st.markdown(
    """
Welcome! This tool generates a valid Power BI project (`.pbip`) from a natural-language
description of the report you want.

**Workflow** (use the pages in the sidebar):
1. **Connect** — point the tool at one or more data sources (SQL databases, warehouses, or
   CSV/Excel files). Multiple sources are combined into a single schema.
2. **Generate** — choose an AI model, describe your report, preview the layout, and download the `.pbip`.
3. **Refine** — iteratively edit the generated report through AI chat (with undo).
4. **Settings** — optionally store your own AI provider API key (encrypted per user).

> Open the downloaded project in **Power BI Desktop (March 2026 or later, PBIR preview
> enabled)** to view and hand-edit it.
"""
)

connectors = st.session_state.get("connectors") or []
if connectors:
    names = ", ".join(f"**{c.get('name', 'unnamed')}**" for c in connectors)
    st.info(f"Active data source{'s' if len(connectors) > 1 else ''}: {names}")
else:
    st.warning("No data source connected yet — start on the **Connect** page.")
