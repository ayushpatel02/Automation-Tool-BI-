"""Streamlit entrypoint. Auth gate + a clean home with the workflow at a glance."""

from __future__ import annotations

import streamlit as st
from components.auth import ensure_authenticated
from components.layout import render_sidebar
from components.sources import get_sources

st.set_page_config(page_title="AI Power BI Generator", page_icon="📊", layout="wide")

client = ensure_authenticated()
render_sidebar(client, active="home")

st.title("📊 Power BI Report Generator")
st.caption("Describe the report you want in plain English — get a ready-to-open Power BI project.")

sources = get_sources(client)

# A compact "what next" banner that adapts to where the user is in the flow.
if not sources:
    st.info("**Start here:** open the **Connect** page in the sidebar to add your first data "
            "source (a CSV/Excel file or a database).", icon="👈")
else:
    names = ", ".join(f"**{s['name']}**" for s in sources)
    st.success(f"{len(sources)} data source{'s' if len(sources) > 1 else ''} ready: {names}", icon="✅")
    st.caption("Your sources are saved to your account — no need to re-upload next time.")

st.markdown("")

# Three-step workflow as tidy cards instead of a wall of text.
c1, c2, c3 = st.columns(3)
with c1:
    st.subheader("1 · Connect")
    st.write("Add a CSV/Excel file or a database. Multiple sources combine into one model.")
with c2:
    st.subheader("2 · Generate")
    st.write("Pick an AI model, describe your report, and download the `.pbip` project.")
with c3:
    st.subheader("3 · Refine")
    st.write("Tweak the report through AI chat — change charts, add measures, undo anytime.")

st.markdown("")
with st.expander("Opening the report in Power BI Desktop"):
    st.markdown(
        "- Use **Power BI Desktop (March 2026 or later)** with the **PBIR** preview option "
        "enabled (File → Options → Preview features).\n"
        "- Your uploaded data is compressed and embedded in the project, so it opens and "
        "loads with no file paths to fix.\n"
        "- Set your own AI provider key (optional) on the **Settings** page."
    )
