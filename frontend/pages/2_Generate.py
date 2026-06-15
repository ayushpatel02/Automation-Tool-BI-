"""Generate page: choose a model, describe the report, watch progress, download .pbip."""

from __future__ import annotations

import base64

import streamlit as st

from api_client import ApiClient, ApiError
from components.auth import ensure_authenticated
from components.preview import render_preview

client: ApiClient = ensure_authenticated()
st.title("2 · Generate a report")

connectors = st.session_state.get("connectors") or []
if not connectors:
    st.warning("Connect a data source first (see the **Connect** page).")
    st.stop()

source_names = ", ".join(f"**{c['name']}**" for c in connectors)
st.caption(f"Data source{'s' if len(connectors) > 1 else ''}: {source_names}")

try:
    models = client.list_models()
except ApiError as exc:
    st.error(str(exc))
    st.stop()

model_labels = {
    f"{m['display_name']}{' (recommended)' if m.get('recommended') else ''}": m["id"]
    for m in models
}
model_label = st.selectbox("AI model", list(model_labels.keys()))
model_id = model_labels[model_label]

project_name = st.text_input("Project name", value="GeneratedReport")
request = st.text_area(
    "Describe the report you want",
    placeholder="e.g. A sales dashboard with total revenue, top 10 products by revenue, "
    "and a monthly revenue trend line.",
    height=120,
)

if st.button("Generate report", type="primary", disabled=not request.strip()):
    body = {
        "model_id": model_id,
        "request": request,
        "credential_ids": [c["credential_id"] for c in connectors if c.get("credential_id")],
        "project_name": project_name,
    }
    try:
        session = client.create_session(body)
    except ApiError as exc:
        st.error(str(exc))
        st.stop()

    st.session_state["session_id"] = session["id"]
    st.session_state["project_name"] = project_name
    st.subheader("Progress")
    log = st.empty()
    lines: list[str] = []
    try:
        for event in client.stream_events(session["id"]):
            stage = event.get("stage", "?")
            status = event.get("status", "")
            line = f"`{stage}` → {status}"
            if status == "retry":
                line += f" (attempt {event.get('attempt')}: {', '.join(event.get('errors', [])[:3])})"
            if event.get("message"):
                line += f" — {event['message']}"
            lines.append(line)
            log.markdown("\n\n".join(lines))
    except ApiError as exc:
        st.error(str(exc))

    final = client.get_session(session["id"])
    if final["status"] == "complete":
        st.success("Report generated and validated.")
    elif final.get("has_download"):
        st.warning(
            "Report generated but did not fully pass validation — review issues below. "
            "You can still download and open it."
        )
    else:
        st.error(final.get("error_message") or "Generation failed.")

st.divider()
session_id = st.session_state.get("session_id")
validation_errors_text = ""
if session_id:
    final = client.get_session(session_id)
    if final.get("has_download"):
        try:
            data = client.download_bytes(session_id)
            st.download_button(
                "⬇ Download .pbip project (zip)",
                data=data,
                file_name=f"{project_name}.zip",
                mime="application/zip",
            )
            st.caption("Then continue to the **Refine** page to edit it with AI chat.")
        except ApiError as exc:
            st.error(str(exc))

        st.subheader("Layout preview")
        st.caption(
            "A wireframe of the generated pages and visuals (structure only — open the "
            ".pbip in Power BI Desktop to see rendered visuals)."
        )
        try:
            render_preview(client.get_preview(session_id))
        except ApiError as exc:
            st.error(str(exc))

    try:
        validation = client.get_validation(session_id)
        with st.expander("Validation details"):
            st.json(validation)
        validation_errors_text = "\n".join(
            f"- {e.get('file', '?')}"
            + (f" ({e['path']})" if e.get("path") else "")
            + f": {e.get('message', '')}"
            for e in validation.get("errors") or []
        )
    except ApiError as exc:
        st.error(str(exc))

st.divider()
with st.expander(
    "🛠 Stuck on an error? Paste it here for help",
    expanded=bool(validation_errors_text),
):
    st.caption(
        "Paste an error message — from this tool's validation output, Power BI Desktop, "
        "DAX, TMDL, or Power Query (M) — or attach a screenshot of the error dialog below. "
        "Get an explanation plus a suggested fix. Pick a smaller/cheaper model to save "
        "tokens, or a stronger one for tricky issues."
    )
    if validation_errors_text:
        st.caption("Pre-filled from this session's validation errors — edit as needed.")
    diag_model_label = st.selectbox(
        "Model for diagnosis", list(model_labels.keys()), key="diag_model_label"
    )
    diag_model_id = model_labels[diag_model_label]
    error_text = st.text_area(
        "Error message",
        value=validation_errors_text,
        height=120,
        key="diag_error_text",
        placeholder="Paste the error or validation message here...",
    )
    screenshot = st.file_uploader(
        "Or attach a screenshot of the error (e.g. the Power BI Desktop dialog)",
        type=["png", "jpg", "jpeg"],
        key="diag_image",
    )
    if screenshot is not None:
        st.image(screenshot, caption=screenshot.name, width=300)
    extra_context = st.text_input(
        "Optional context (what were you trying to do?)", key="diag_context"
    )
    if st.button("Get help", disabled=not (error_text.strip() or screenshot)):
        try:
            image_b64 = base64.b64encode(screenshot.getvalue()).decode() if screenshot else None
            image_mime = screenshot.type if screenshot else None
            result = client.diagnose_error(
                diag_model_id, error_text, extra_context or None, image_b64, image_mime
            )
            st.markdown(result["answer"])
        except ApiError as exc:
            st.error(f"Could not get help: {exc}")
