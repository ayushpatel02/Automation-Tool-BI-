"""Connect page: add or remove data sources (persisted to your account)."""

from __future__ import annotations

from pathlib import Path

import streamlit as st
from api_client import ApiClient, ApiError
from components.auth import ensure_authenticated
from components.layout import render_sidebar
from components.sources import get_sources, refresh_sources

client: ApiClient = ensure_authenticated()
render_sidebar(client, active="connect")

st.title("🔌 Connect data sources")
st.caption(
    "Sources are saved to your account, so you only add a file or database once — "
    "it's still here (data and all) next time you sign in."
)

CONNECTOR_TYPES = {
    "CSV file": "csv",
    "Excel file": "excel",
    "PostgreSQL": "postgresql",
    "MySQL": "mysql",
    "SQL Server": "sqlserver",
    "Snowflake": "snowflake",
    "BigQuery": "bigquery",
    "Databricks": "databricks",
}
_TYPE_ICON = {
    "postgresql": "🐘", "mysql": "🐬", "sqlserver": "🗄", "snowflake": "❄",
    "bigquery": "🔎", "databricks": "🧱", "csv": "📄", "excel": "📊",
}

# ── Saved sources ─────────────────────────────────────────────────────────────
sources = get_sources(client)
if sources:
    st.subheader("Your data sources")
    for s in sources:
        icon = _TYPE_ICON.get(s.get("type", ""), "•")
        col_name, col_del = st.columns([6, 1])
        col_name.markdown(f"{icon}  **{s['name']}**  ·  _{s['type']}_")
        if col_del.button("Remove", key=f"del_{s['credential_id']}"):
            try:
                client.delete_connector(s["credential_id"])
                refresh_sources(client)
                st.toast(f"Removed {s['name']}")
                st.rerun()
            except ApiError as exc:
                st.error(str(exc))
    st.divider()

# ── Add a new source ──────────────────────────────────────────────────────────
with st.expander("➕ Add a data source", expanded=not sources):
    label = st.selectbox("Source type", list(CONNECTOR_TYPES.keys()))
    ctype = CONNECTOR_TYPES[label]
    name = st.text_input("Friendly name", value=f"My {label}")

    config: dict = {"type": ctype, "name": name, "extra": {}}

    if ctype in ("csv", "excel"):
        accepted = ["csv"] if ctype == "csv" else ["xlsx", "xls"]
        uploaded = st.file_uploader("Upload a file", type=accepted)
        if uploaded is not None:
            upload_key = f"uploaded:{uploaded.name}:{uploaded.size}"
            if st.session_state.get("uploaded_key") != upload_key:
                try:
                    meta = client.upload_file(
                        uploaded.name,
                        uploaded.getvalue(),
                        uploaded.type or "application/octet-stream",
                    )
                    st.session_state["uploaded_key"] = upload_key
                    st.session_state["uploaded_meta"] = meta
                except ApiError as exc:
                    st.error(str(exc))
                    st.session_state.pop("uploaded_meta", None)
            meta = st.session_state.get("uploaded_meta")
            if meta:
                kb = meta["size_bytes"] / 1024
                st.success(f"Uploaded **{meta['original_name']}** ({kb:.1f} KB)")
                config["extra"]["file_path"] = meta["file_path"]
                config["extra"]["original_name"] = meta["original_name"]
                if not name.strip() or name == f"My {label}":
                    config["name"] = Path(meta["original_name"]).stem
    else:
        col1, col2 = st.columns(2)
        config["host"] = col1.text_input("Host", value="localhost")
        config["port"] = col2.number_input("Port", value=5432, step=1) or None
        config["database"] = st.text_input("Database / catalog")
        config["schema_name"] = st.text_input("Schema (optional)") or None
        config["username"] = st.text_input("Username")
        config["password"] = st.text_input("Password", type="password")

        if ctype == "snowflake":
            config["extra"]["account"] = st.text_input("Account identifier")
            config["extra"]["warehouse"] = st.text_input("Warehouse")
        elif ctype == "bigquery":
            config["extra"]["project"] = st.text_input("GCP project id")
        elif ctype == "databricks":
            config["extra"]["http_path"] = st.text_input("HTTP path")
            config["extra"]["access_token"] = st.text_input("Access token", type="password")

    col_test, col_save = st.columns(2)
    if col_test.button("Test connection", use_container_width=True):
        try:
            result = client.test_connector(config)
            (st.success if result["ok"] else st.error)(result["message"])
        except ApiError as exc:
            st.error(str(exc))

    if col_save.button("Save source", type="primary", use_container_width=True):
        try:
            result = client.test_connector(config)
            if not result["ok"]:
                st.error(f"Connection failed: {result['message']}")
            else:
                client.save_connector(config)
                st.session_state.pop("uploaded_key", None)
                st.session_state.pop("uploaded_meta", None)
                refresh_sources(client)
                st.toast(f"Saved {config['name']}")
                st.rerun()
        except ApiError as exc:
            st.error(str(exc))

if sources:
    st.caption("Ready? Head to the **Generate** page in the sidebar.")
