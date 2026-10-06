"""Connect page: configure and test a data source."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from api_client import ApiClient, ApiError
from components.auth import ensure_authenticated

client: ApiClient = ensure_authenticated()
st.title("1 · Connect data sources")
st.caption(
    "Connect one or more data sources. Every source added below will be combined into a "
    "single schema for the next report you generate — handy for, e.g., a sales database "
    "plus a marketing-spend CSV."
)

CONNECTOR_TYPES = {
    "PostgreSQL": "postgresql",
    "MySQL": "mysql",
    "SQL Server": "sqlserver",
    "Snowflake": "snowflake",
    "BigQuery": "bigquery",
    "Databricks": "databricks",
    "CSV file": "csv",
    "Excel file": "excel",
}

label = st.selectbox("Source type", list(CONNECTOR_TYPES.keys()))
ctype = CONNECTOR_TYPES[label]
name = st.text_input("Friendly name", value=f"My {label}")

config: dict = {"type": ctype, "name": name, "extra": {}}

if ctype in ("csv", "excel"):
    accepted = ["csv"] if ctype == "csv" else ["xlsx", "xls"]
    uploaded = st.file_uploader(
        "Upload a file",
        type=accepted,
        help="The file is stored on the backend and used as the source for this connector.",
    )
    if uploaded is not None:
        upload_key = f"uploaded:{uploaded.name}:{uploaded.size}"
        if st.session_state.get("uploaded_key") != upload_key:
            try:
                meta = client.upload_file(
                    uploaded.name, uploaded.getvalue(), uploaded.type or "application/octet-stream"
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

if col_test.button("Test connection"):
    try:
        result = client.test_connector(config)
        if result["ok"]:
            st.success(result["message"])
        else:
            st.error(result["message"])
    except ApiError as exc:
        st.error(str(exc))

if col_save.button("Add this source", type="primary"):
    try:
        result = client.test_connector(config)
        if not result["ok"]:
            st.error(f"Connection failed: {result['message']}")
        else:
            saved = client.save_connector(config)
            sources = st.session_state.setdefault("connectors", [])
            entry = {"name": config["name"], "type": ctype, "credential_id": saved["id"]}
            # Re-adding a source with the same name+type updates it instead of duplicating.
            sources[:] = [
                s for s in sources if (s["name"], s["type"]) != (entry["name"], entry["type"])
            ]
            sources.append(entry)
            st.success(
                f"Added **{entry['name']}**. Connect another source above, or head to the "
                "**Generate** page."
            )
    except ApiError as exc:
        st.error(str(exc))

st.divider()
connectors = st.session_state.get("connectors", [])
if connectors:
    st.subheader("Active data sources")
    for i, c in enumerate(connectors):
        col_name, col_remove = st.columns([5, 1])
        col_name.markdown(f"**{c['name']}** · {c['type']}")
        if col_remove.button("Remove", key=f"remove_source_{i}"):
            connectors.pop(i)
            st.rerun()
    st.caption(
        f"{len(connectors)} source(s) will be combined for the next report you generate."
    )
else:
    st.caption("No data sources connected yet — add one above.")
