"""Render a wireframe layout preview of a generated report.

The backend returns each page's visuals with pixel positions; we draw them as an SVG
(scaled to a readable width) inside an iframe so Streamlit renders the raw markup reliably.
This is a structural preview, not a data render — actual visuals require Power BI Desktop.
"""

from __future__ import annotations

import html

import streamlit as st
import streamlit.components.v1 as components

_DISPLAY_WIDTH = 640  # px the SVG is scaled to in the UI


def render_preview(preview: dict) -> None:
    pages = preview.get("pages", [])
    if not pages:
        st.caption("No pages to preview.")
        return
    for page in pages:
        st.markdown(f"**{html.escape(str(page.get('name', 'Page')))}**")
        svg, height = _page_svg(page)
        components.html(svg, height=int(height) + 8)


def _page_svg(page: dict) -> tuple[str, float]:
    pw = max(1, int(page.get("width", 1280)))
    ph = max(1, int(page.get("height", 720)))
    scale = _DISPLAY_WIDTH / pw
    sw, sh = pw * scale, ph * scale

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{sw:.0f}" height="{sh:.0f}" '
        f'viewBox="0 0 {sw:.0f} {sh:.0f}" style="background:#fff;border:1px solid #d0d0d0">'
    ]
    for v in page.get("visuals", []):
        x, y = v.get("x", 0) * scale, v.get("y", 0) * scale
        w, h = max(8, v.get("width", 200) * scale), max(8, v.get("height", 150) * scale)
        title = html.escape(str(v.get("title", ""))[:42])
        vtype = html.escape(str(v.get("type", "")))
        fields = html.escape(", ".join(v.get("fields", []))[:60])
        parts.append(
            f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" '
            f'rx="4" fill="#eef2ff" stroke="#6366f1" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x + 6:.0f}" y="{y + 18:.0f}" font-family="sans-serif" '
            f'font-size="12" font-weight="600" fill="#312e81">{title}</text>'
        )
        parts.append(
            f'<text x="{x + 6:.0f}" y="{y + 34:.0f}" font-family="sans-serif" '
            f'font-size="10" fill="#6b7280">{vtype}</text>'
        )
        if fields:
            parts.append(
                f'<text x="{x + 6:.0f}" y="{y + 50:.0f}" font-family="sans-serif" '
                f'font-size="9" fill="#9ca3af">{fields}</text>'
            )
    parts.append("</svg>")
    return "".join(parts), sh
