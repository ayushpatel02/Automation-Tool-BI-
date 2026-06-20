"""Generate page: choose a model, describe the report, watch progress, download .pbip."""

from __future__ import annotations

import base64
import threading
import time

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


# ── Streaming helpers ───────────────────────────────────────────────────────

def _format_event(event: dict) -> str:
    stage = event.get("stage", "?")
    status = event.get("status", "")
    labels = {
        "gen_model": "Generating semantic model",
        "validate_model": "Validating semantic model",
        "gen_report": "Generating report",
        "validate_report": "Validating report",
        "self_test": "Self-testing report",
        "assemble": "Assembling .pbip",
        "error": "Error",
    }
    if stage == "self_test":
        if status == "start":
            return "**Self-test** — running..."
        if status in ("ok", "done") and event.get("passed"):
            return "**Self-test** — all checks passed ✓"
        if status == "issues":
            errs = event.get("errors", [])
            tail = ", ".join(errs[:3]) + (" …" if len(errs) > 3 else "")
            return f"**Self-test** — issues (attempt {event.get('attempt', 1)}): {tail}"
        if status == "repairing":
            return f"**Self-test** — repairing (attempt {event.get('attempt', 1)})..."
        if status == "done":
            remaining = event.get("remaining", [])
            return "**Self-test** — complete with issues: " + ", ".join(remaining[:3])
        if status == "error":
            return f"**Self-test** — error: {event.get('message', '')}"
    label = labels.get(stage, stage)
    line = f"`{label}` → {status}"
    if status == "retry":
        errs = ", ".join(event.get("errors", [])[:3])
        line += f" (attempt {event.get('attempt')}: {errs})"
    if event.get("message"):
        line += f" — {event['message']}"
    return line


def _start_stream(session_id: str) -> None:
    """Spawn a daemon thread that accumulates SSE events into session_state."""
    stop_ev = threading.Event()
    done_ev = threading.Event()
    events: list[dict] = []  # appended by bg thread; list.append is GIL-atomic

    def _worker() -> None:
        try:
            for ev in client.stream_events(session_id):
                if stop_ev.is_set():
                    break
                events.append(ev)
        except Exception:  # noqa: BLE001
            pass
        finally:
            done_ev.set()

    st.session_state["_gen_session_id"] = session_id
    st.session_state["_gen_events"] = events
    st.session_state["_gen_stop"] = stop_ev
    st.session_state["_gen_done"] = done_ev
    st.session_state["_gen_in_progress"] = True
    st.session_state["_gen_cancelled"] = False
    threading.Thread(target=_worker, daemon=True).start()


def _draw_progress(log_placeholder) -> None:
    events: list[dict] = st.session_state.get("_gen_events") or []
    lines = [_format_event(e) for e in events]
    log_placeholder.markdown("\n\n".join(lines) if lines else "_Starting…_")


# ── Streaming state machine ─────────────────────────────────────────────────
# This runs at the TOP of each Streamlit script execution.
# While _gen_in_progress is True the page auto-reruns every 0.5 s so the
# stop button is rendered and events accumulate in the live log.

_in_progress = st.session_state.get("_gen_in_progress", False)

if _in_progress:
    done_ev: threading.Event = st.session_state["_gen_done"]

    hdr_col, stop_col = st.columns([5, 1])
    with hdr_col:
        st.subheader("Generating…")
    with stop_col:
        if st.button("Stop", type="secondary", key="stop_gen"):
            st.session_state["_gen_stop"].set()
            st.session_state["_gen_in_progress"] = False
            st.session_state["_gen_cancelled"] = True
            st.rerun()

    log_ph = st.empty()
    _draw_progress(log_ph)

    if done_ev.is_set():
        # Stream finished — transition out of in-progress state
        st.session_state["_gen_in_progress"] = False
        sid = st.session_state.get("_gen_session_id", "")
        if sid:
            try:
                final = client.get_session(sid)
                if final["status"] == "complete":
                    st.success("Report generated and validated.")
                elif final.get("has_download"):
                    st.warning(
                        "Report generated but did not fully pass all checks — "
                        "review the self-test results below."
                    )
                else:
                    st.error(final.get("error_message") or "Generation failed.")
            except ApiError as exc:
                st.error(str(exc))
    else:
        # Not done yet: pause then rerun to refresh the log and keep stop button live
        time.sleep(0.5)
        st.rerun()

elif st.session_state.get("_gen_cancelled"):
    st.info("Generation stopped. The server is still completing the run in the background — "
            "check back shortly or click Re-test once a download appears.")

# ── Generate button (hidden while generating) ────────────────────────────────
if not _in_progress and not st.session_state.get("_gen_cancelled"):
    if st.button("Generate report", type="primary", disabled=not request.strip()):
        body = {
            "model_id": model_id,
            "request": request,
            "credential_ids": [
                c["credential_id"] for c in connectors if c.get("credential_id")
            ],
            "project_name": project_name,
        }
        try:
            session = client.create_session(body)
        except ApiError as exc:
            st.error(str(exc))
            st.stop()
        st.session_state["session_id"] = session["id"]
        st.session_state["project_name"] = project_name
        _start_stream(session["id"])
        st.rerun()


# ── Results section ──────────────────────────────────────────────────────────

def _render_self_test(st_data: dict) -> None:
    passed = st_data.get("passed", False)
    layers = st_data.get("layers_run", [])
    findings = st_data.get("findings", [])
    auto_fixed = st_data.get("auto_fixed", [])
    attempts = st_data.get("attempts", 1)

    errors = [f for f in findings if f.get("severity") == "error"]
    warnings = [f for f in findings if f.get("severity") == "warning"]

    if passed:
        st.success(f"Self-test passed — all {len(layers)} layer(s) clean.")
    else:
        st.error(
            f"Self-test found {len(errors)} error(s)"
            + (f" and {len(warnings)} warning(s)." if warnings else ".")
        )

    layer_labels = {"deterministic": "Linter", "te2": "TE2 compile", "llm_review": "LLM review"}
    ran_set = set(layers)
    badge_parts = [
        (f"✓ `{lbl}`" if key in ran_set else f"– `{lbl}` (skipped)")
        for key, lbl in layer_labels.items()
    ]
    st.caption("Layers: " + "  ·  ".join(badge_parts))

    if attempts > 1:
        st.caption(f"Auto-repair ran {attempts} attempt(s).")

    if auto_fixed:
        with st.expander(f"Auto-fixes applied ({len(auto_fixed)})", expanded=True):
            for fix in auto_fixed:
                st.markdown(f"- {fix}")

    if errors:
        with st.expander(f"Errors ({len(errors)})", expanded=True):
            for f in errors:
                loc = f.get("file") or ""
                cat = f.get("category") or ""
                layer_tag = f"[{f.get('layer', '')}] " if f.get("layer") else ""
                prefix = f"`{loc}` " if loc else ""
                st.markdown(f"- {prefix}{layer_tag}**{cat}**: {f['message']}")

    if warnings:
        with st.expander(f"Warnings ({len(warnings)})", expanded=False):
            for f in warnings:
                loc = f.get("file") or ""
                cat = f.get("category") or ""
                layer_tag = f"[{f.get('layer', '')}] " if f.get("layer") else ""
                prefix = f"`{loc}` " if loc else ""
                st.markdown(f"- {prefix}{layer_tag}**{cat}**: {f['message']}")


st.divider()
session_id = st.session_state.get("session_id")
validation_errors_text = ""
if session_id:
    try:
        final = client.get_session(session_id)
    except ApiError:
        final = {}

    if final.get("has_download"):
        col_dl, col_retest = st.columns([3, 1])
        with col_dl:
            try:
                data = client.download_bytes(session_id)
                st.download_button(
                    "⬇ Download .pbip project (zip)",
                    data=data,
                    file_name=f"{project_name}.zip",
                    mime="application/zip",
                )
                # Show data-file note for CSV/Excel sources.
                _file_types = {"csv", "excel"}
                if any(c.get("type") in _file_types for c in connectors):
                    st.info(
                        "**Your data is embedded in the model.** The uploaded file's "
                        "contents are baked directly into the .pbip, so it opens in Power BI "
                        "Desktop and loads immediately — no file paths to fix. (Power BI caps "
                        "embedded data at ~10 MB, so larger files are bundled in the zip "
                        "instead; if Power BI can't find one, open **Transform Data → Data "
                        "source settings** and point it at the file in the extracted folder.)",
                        icon="📎",
                    )
                st.caption("Then continue to the **Refine** page to edit it with AI chat.")
            except ApiError as exc:
                st.error(str(exc))
        with col_retest:
            if st.button(
                "Re-test report",
                help="Re-run the full self-test and auto-repair loop",
                disabled=_in_progress,
            ):
                try:
                    client.run_self_test(session_id)
                    _start_stream(session_id)
                    st.rerun()
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

        st_data = validation.get("self_test")
        if st_data:
            st.subheader("Self-test report")
            _render_self_test(st_data)

        for f in (st_data.get("findings") or [] if st_data else []):
            if f.get("severity") == "error":
                loc = f.get("file") or "?"
                validation_errors_text += f"- {loc}: {f.get('message', '')}\n"

        if not validation_errors_text:
            report_val = validation.get("report") or {}
            validation_errors_text = "\n".join(
                f"- {e.get('file', '?')}"
                + (f" ({e['path']})" if e.get("path") else "")
                + f": {e.get('message', '')}"
                for e in report_val.get("errors") or []
            )

        with st.expander("Full validation details (JSON)"):
            st.json(validation)
    except ApiError as exc:
        st.error(str(exc))

st.divider()
with st.expander(
    "Stuck on an error? Paste it here for help",
    expanded=bool(validation_errors_text),
):
    st.caption(
        "Paste an error message — from this tool's validation output, Power BI Desktop, "
        "DAX, TMDL, or Power Query (M) — or attach a screenshot of the error dialog below."
    )
    if validation_errors_text:
        st.caption("Pre-filled from this session's self-test errors — edit as needed.")
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
