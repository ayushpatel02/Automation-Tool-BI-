"""Refine page: iteratively edit the generated report through AI chat."""

from __future__ import annotations

import streamlit as st

from api_client import ApiClient, ApiError
from components.auth import ensure_authenticated
from components.preview import render_preview

client: ApiClient = ensure_authenticated()
st.title("3 · Refine the report")

session_id = st.session_state.get("session_id")
if not session_id:
    st.warning("Generate a report first (see the **Generate** page).")
    st.stop()

st.caption(f"Editing session `{session_id}`")
st.markdown(
    "Describe a change in plain language, e.g. *“make the revenue chart a line chart and "
    "add a 12-month rolling average”*. Only the affected parts are regenerated and "
    "re-validated."
)

history = st.session_state.setdefault("refine_log", [])
for entry in history:
    st.chat_message(entry["role"]).write(entry["text"])

edit = st.chat_input("Describe your edit")
if edit:
    history.append({"role": "user", "text": edit})
    st.chat_message("user").write(edit)
    try:
        client.refine(session_id, edit)
    except ApiError as exc:
        st.error(str(exc))
        st.stop()

    with st.chat_message("assistant"):
        log = st.empty()
        lines: list[str] = []
        try:
            for event in client.stream_events(session_id):
                stage = event.get("stage", "?")
                status = event.get("status", "")
                line = f"`{stage}` → {status}"
                if status == "retry":
                    line += f" (attempt {event.get('attempt')})"
                if event.get("scope"):
                    line += f" — scope: {', '.join(event['scope'])}"
                if event.get("message"):
                    line += f" — {event['message']}"
                lines.append(line)
                log.markdown("\n\n".join(lines))
        except ApiError as exc:
            st.error(str(exc))

    final = client.get_session(session_id)
    msg = (
        "Applied and re-validated. Download the updated project below."
        if final.get("has_download")
        else (final.get("error_message") or "Edit failed.")
    )
    history.append({"role": "assistant", "text": msg})
    st.chat_message("assistant").write(msg)

final = client.get_session(session_id)
if final.get("has_download"):
    col_dl, col_undo = st.columns([2, 1])
    try:
        data = client.download_bytes(session_id)
        project_name = (
            final.get("project_name") or st.session_state.get("project_name") or "GeneratedReport"
        )
        col_dl.download_button(
            "⬇ Download updated .pbip (zip)",
            data=data,
            file_name=f"{project_name}.zip",
            mime="application/zip",
        )
    except ApiError as exc:
        st.error(str(exc))

    try:
        versions = client.get_history(session_id).get("count", 0)
    except ApiError:
        versions = 0
    if col_undo.button(f"↩ Undo last edit ({versions})", disabled=versions == 0):
        try:
            client.revert(session_id)
            st.session_state["refine_log"] = []
            st.toast("Reverted to the previous version.")
            st.rerun()
        except ApiError as exc:
            st.error(str(exc))

    st.subheader("Layout preview")
    try:
        render_preview(client.get_preview(session_id))
    except ApiError as exc:
        st.error(str(exc))
