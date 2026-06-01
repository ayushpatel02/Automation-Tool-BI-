"""Connect page: configure and test a data source."""

from __future__ import annotations

import streamlit as st

from api_client import ApiClient, ApiError
from components.auth import ensure_authenticated

client: ApiClient = ensure_authenticated()
st.title("1 · Connect a data source")

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
    config["extra"]["file_path"] = st.text_input(
        "Absolute file path (on the backend host)",
        help="The file must be readable by the backend process.",
    )
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

if col_save.button("Use this source", type="primary"):
    try:
        result = client.test_connector(config)
        if not result["ok"]:
            st.error(f"Connection failed: {result['message']}")
        else:
            saved = client.save_connector(config)
            st.session_state["connector"] = {**config, "credential_id": saved["id"]}
            st.success("Connected. Head to the **Generate** page.")
    except ApiError as exc:
        st.error(str(exc))

if st.session_state.get("connector"):
    st.divider()
    st.caption(f"Active source: {st.session_state['connector']['name']}")
