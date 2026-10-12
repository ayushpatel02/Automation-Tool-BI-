"""Generate page: choose a model, describe the report, watch progress, download .pbip."""

from __future__ import annotations

import base64
import threading
import time

import streamlit as st
from api_client import ApiClient, ApiError
from components.auth import ensure_authenticated
from components.layout import render_sidebar
from components.preview import render_preview
from components.sources import get_sources

client: ApiClient = ensure_authenticated()
render_sidebar(client, active="generate")

st.title("✨ Generate a report")

connectors = get_sources(client)
if not connectors:
    st.warning("Add a data source first — open the **Connect** page in the sidebar.", icon="👈")
    st.stop()

try:
    models = client.list_models()
except ApiError as exc:
    st.error(str(exc))
    st.stop()

model_labels = {
    f"{m['display_name']}{' (recommended)' if m.get('recommended') else ''}": m["id"]
    for m in models
}

# ── Inputs ────────────────────────────────────────────────────────────────────
source_names = ", ".join(f"**{c['name']}**" for c in connectors)
st.caption(f"Using data from: {source_names}")

request = st.text_area(
    "Describe the report you want",
    placeholder="e.g. A sales dashboard with total revenue and profit as cards, sales by "
    "category as a bar chart, and a monthly revenue trend line.",
    height=110,
)
with st.expander("Options"):
    model_label = st.selectbox("AI model", list(model_labels.keys()))
    project_name = st.text_input("Project name", value="GeneratedReport")
model_id = model_labels[model_label]


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
        except Exception:  # noqa: BLE001, S110 — bg thread must never crash the UI
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


# ── Streaming state machine (runs at top of each rerun) ───────────────────────
_in_progress = st.session_state.get("_gen_in_progress", False)

if _in_progress:
    done_ev: threading.Event = st.session_state["_gen_done"]

    if done_ev.is_set():
        # Finished: flip state and fall through to the results section THIS run (so the
        # download tabs render immediately instead of waiting for another interaction).
        st.session_state["_gen_in_progress"] = False
        _in_progress = False
        sid = st.session_state.get("_gen_session_id", "")
        try:
            final = client.get_session(sid) if sid else {}
            if final.get("status") == "complete":
                st.success("Report generated and validated.")
            elif final.get("has_download"):
                st.warning("Report generated but didn't pass every check — see the "
                           "**Self-test** tab below.")
            else:
                st.error(final.get("error_message") or "Generation failed.")
        except ApiError as exc:
            st.error(str(exc))
    else:
        # Still running: show live progress + a Stop button, then poll again.
        hdr_col, stop_col = st.columns([5, 1])
        hdr_col.subheader("Generating…")
        if stop_col.button("Stop", type="secondary", key="stop_gen"):
            st.session_state["_gen_stop"].set()
            st.session_state["_gen_in_progress"] = False
            st.session_state["_gen_cancelled"] = True
            st.rerun()
        with st.status("Working…", expanded=True):
            _draw_progress(st.empty())
        time.sleep(0.5)
        st.rerun()

elif st.session_state.get("_gen_cancelled"):
    st.info("Generation stopped. The server may still be finishing in the background — "
            "check back shortly.")

# ── Generate button ───────────────────────────────────────────────────────────
_show_generate = not _in_progress and not st.session_state.get("_gen_cancelled")
if _show_generate and st.button(
    "✨ Generate report", type="primary", disabled=not request.strip(),
    use_container_width=True,
):
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
    _start_stream(session["id"])
    st.rerun()


# ── Results ───────────────────────────────────────────────────────────────────

def _render_self_test(st_data: dict) -> None:
    layers = st_data.get("layers_run", [])
    findings = st_data.get("findings", [])
    auto_fixed = st_data.get("auto_fixed", [])
    attempts = st_data.get("attempts", 1)
    errors = [f for f in findings if f.get("severity") == "error"]
    warnings = [f for f in findings if f.get("severity") == "warning"]

    if st_data.get("passed"):
        st.success(f"Passed — all {len(layers)} layer(s) clean.")
    else:
        st.error(f"Found {len(errors)} error(s)"
                 + (f" and {len(warnings)} warning(s)." if warnings else "."))

    layer_labels = {"deterministic": "Linter", "te2": "TE2 compile", "llm_review": "LLM review"}
    ran = set(layers)
    st.caption("Layers: " + "  ·  ".join(
        (f"✓ {lbl}" if k in ran else f"– {lbl} (skipped)") for k, lbl in layer_labels.items()
    ) + (f"   ·   auto-repair ×{attempts}" if attempts > 1 else ""))

    def _list(items, title, expanded):
        with st.expander(f"{title} ({len(items)})", expanded=expanded):
            for f in items:
                loc = f"`{f['file']}` " if f.get("file") else ""
                st.markdown(f"- {loc}**{f.get('category', '')}**: {f['message']}")

    if auto_fixed:
        with st.expander(f"Auto-fixes applied ({len(auto_fixed)})"):
            for fix in auto_fixed:
                st.markdown(f"- {fix}")
    if errors:
        _list(errors, "Errors", True)
    if warnings:
        _list(warnings, "Warnings", False)


session_id = st.session_state.get("session_id")
validation_errors_text = ""

if session_id and not _in_progress:
    try:
        final = client.get_session(session_id)
    except ApiError:
        final = {}

    # Pull validation once for the self-test tab + diagnostics pre-fill.
    try:
        validation = client.get_validation(session_id)
    except ApiError:
        validation = {}
    st_data = validation.get("self_test") if validation else None
    if st_data:
        for f in (st_data.get("findings") or []):
            if f.get("severity") == "error":
                validation_errors_text += f"- {f.get('file', '?')}: {f.get('message', '')}\n"

    if final.get("has_download"):
        st.divider()
        tab_dl, tab_preview, tab_test, tab_help = st.tabs(
            ["⬇ Download", "👁 Preview", "✅ Self-test", "🩺 Get help"]
        )

        with tab_dl:
            try:
                data = client.download_bytes(session_id)
                st.download_button(
                    "⬇ Download .pbip project (zip)", data=data,
                    file_name=f"{project_name}.zip", mime="application/zip",
                    type="primary", use_container_width=True,
                )
            except ApiError as exc:
                st.error(str(exc))
            if any(c.get("type") in {"csv", "excel"} for c in connectors):
                st.caption("📎 Your data is compressed and embedded in the project — it opens "
                           "in Power BI Desktop and loads with no file paths to fix.")
            st.caption("Next: open the **Refine** page to edit the report with AI chat.")
            if st.button("🔄 Re-test report", disabled=_in_progress,
                         help="Re-run the full self-test and auto-repair loop"):
                try:
                    client.run_self_test(session_id)
                    _start_stream(session_id)
                    st.rerun()
                except ApiError as exc:
                    st.error(str(exc))

        with tab_preview:
            st.caption("Wireframe of the pages and visuals (structure only).")
            try:
                render_preview(client.get_preview(session_id))
            except ApiError as exc:
                st.error(str(exc))

        with tab_test:
            if st_data:
                _render_self_test(st_data)
                with st.expander("Full validation details (JSON)"):
                    st.json(validation)
            else:
                st.caption("No self-test report for this session yet.")
    else:
        st.divider()
        tab_help = None
else:
    tab_help = None


# ── Diagnostics (inside the Get help tab, or standalone when no report yet) ────

def _diagnostics_body(container) -> None:
    with container:
        st.caption("Paste an error from Power BI Desktop, DAX, TMDL, or this tool — or attach "
                   "a screenshot — and get an explanation plus a fix.")
        if validation_errors_text:
            st.caption("Pre-filled from this session's self-test errors.")
        diag_label = st.selectbox("Model for diagnosis", list(model_labels.keys()),
                                  key="diag_model_label")
        error_text = st.text_area("Error message", value=validation_errors_text, height=110,
                                  key="diag_error_text", placeholder="Paste the error here…")
        screenshot = st.file_uploader("Or attach a screenshot", type=["png", "jpg", "jpeg"],
                                      key="diag_image")
        if screenshot is not None:
            st.image(screenshot, caption=screenshot.name, width=280)
        extra_context = st.text_input("Optional: what were you trying to do?", key="diag_context")
        if st.button("Get help", disabled=not (error_text.strip() or screenshot)):
            try:
                image_b64 = base64.b64encode(screenshot.getvalue()).decode() if screenshot else None
                image_mime = screenshot.type if screenshot else None
                result = client.diagnose_error(
                    model_labels[diag_label], error_text, extra_context or None,
                    image_b64, image_mime,
                )
                st.markdown(result["answer"])
            except ApiError as exc:
                st.error(f"Could not get help: {exc}")


if tab_help is not None:
    _diagnostics_body(tab_help)
elif not _in_progress:
    with st.expander("🩺 Stuck on an error? Get help", expanded=bool(validation_errors_text)):
        _diagnostics_body(st.container())
