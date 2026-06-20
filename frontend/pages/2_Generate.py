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


def _stage_label(stage: str, status: str) -> str:
    icons = {
        "gen_model": "Generating semantic model",
        "validate_model": "Validating semantic model",
        "gen_report": "Generating report",
        "validate_report": "Validating report",
        "self_test": "Self-testing report",
        "assemble": "Assembling .pbip",
        "error": "Error",
    }
    return icons.get(stage, stage)


def _stream_progress(session_id: str) -> None:
    log = st.empty()
    lines: list[str] = []
    try:
        for event in client.stream_events(session_id):
            stage = event.get("stage", "?")
            status = event.get("status", "")
            label = _stage_label(stage, status)

            if stage == "self_test":
                if status == "start":
                    lines.append("**Self-test** — running...")
                elif status == "ok":
                    lines.append(f"**Self-test** — passed (attempt {event.get('attempt', 1)})")
                elif status == "issues":
                    errs = event.get("errors", [])
                    lines.append(
                        f"**Self-test** — issues found (attempt {event.get('attempt', 1)}): "
                        + ", ".join(errs[:3])
                        + (" …" if len(errs) > 3 else "")
                    )
                elif status == "repairing":
                    lines.append(
                        f"**Self-test** — repairing (attempt {event.get('attempt', 1)})..."
                    )
                elif status == "done":
                    if event.get("passed"):
                        lines.append("**Self-test** — all checks passed ✓")
                    else:
                        remaining = event.get("remaining", [])
                        lines.append(
                            "**Self-test** — complete with remaining issues: "
                            + ", ".join(remaining[:3])
                        )
                elif status == "error":
                    lines.append(f"**Self-test** — error: {event.get('message', '')}")
            else:
                line = f"`{label}` → {status}"
                if status == "retry":
                    line += f" (attempt {event.get('attempt')}: {', '.join(event.get('errors', [])[:3])})"
                if event.get("message"):
                    line += f" — {event['message']}"
                lines.append(line)

            log.markdown("\n\n".join(lines))
    except ApiError as exc:
        st.error(str(exc))


def _render_self_test(st_data: dict) -> None:
    """Render a SelfTestReport dict as a structured pass/fail summary."""
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
            f"Self-test found {len(errors)} error(s) "
            + (f"and {len(warnings)} warning(s)." if warnings else ".")
        )

    # Layer badges
    layer_labels = {"deterministic": "Linter", "te2": "TE2 compile", "llm_review": "LLM review"}
    badges = "  ".join(
        f"`{layer_labels.get(l, l)}`" for l in ["deterministic", "te2", "llm_review"]
    )
    ran_set = set(layers)
    badge_parts = []
    for key, label in layer_labels.items():
        if key in ran_set:
            badge_parts.append(f"✓ `{label}`")
        else:
            badge_parts.append(f"– `{label}` (skipped)")
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
    _stream_progress(session["id"])

    final = client.get_session(session["id"])
    if final["status"] == "complete":
        st.success("Report generated and validated.")
    elif final.get("has_download"):
        st.warning(
            "Report generated but did not fully pass all checks — review the self-test "
            "results below. You can still download and open it."
        )
    else:
        st.error(final.get("error_message") or "Generation failed.")

st.divider()
session_id = st.session_state.get("session_id")
validation_errors_text = ""
if session_id:
    final = client.get_session(session_id)
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
                st.caption("Then continue to the **Refine** page to edit it with AI chat.")
            except ApiError as exc:
                st.error(str(exc))
        with col_retest:
            if st.button("Re-test report", help="Re-run the full self-test and auto-repair loop"):
                try:
                    client.run_self_test(session_id)
                except ApiError as exc:
                    st.error(str(exc))
                else:
                    st.subheader("Self-test progress")
                    _stream_progress(session_id)
                    st.rerun()

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

        # Self-test report (shown prominently if present)
        st_data = validation.get("self_test")
        if st_data:
            st.subheader("Self-test report")
            _render_self_test(st_data)

        # Pre-fill diagnostics from self-test errors
        for f in (st_data.get("findings") or [] if st_data else []):
            if f.get("severity") == "error":
                loc = f.get("file") or "?"
                validation_errors_text += f"- {loc}: {f.get('message', '')}\n"

        # Fallback: also pull from report validation errors if no self-test
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
        "DAX, TMDL, or Power Query (M) — or attach a screenshot of the error dialog below. "
        "Get an explanation plus a suggested fix. Pick a smaller/cheaper model to save "
        "tokens, or a stronger one for tricky issues."
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
