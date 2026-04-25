"""Markdown preview tab — inspect the RAG output."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.config import resolve_path
from src.database import list_comparisons


def render_markdown_tab() -> None:
    comparisons = list_comparisons()
    if not comparisons:
        st.info("No comparisons ingested yet.")
        return

    labels = {
        c["id"]: f"#{c['id']} — {c['product_a']} vs {c['product_b']}"
        for c in comparisons
    }
    picked = st.selectbox(
        "Select a comparison",
        options=[c["id"] for c in comparisons],
        format_func=lambda i: labels[i],
    )
    row = next(c for c in comparisons if c["id"] == picked)
    md_path_raw = row.get("markdown_path")
    if not md_path_raw:
        st.warning("No Markdown file on record for this comparison.")
        return

    md_path = resolve_path(md_path_raw)
    if not md_path.exists():
        st.error(f"Markdown file missing at {md_path}")
        return

    st.caption(f"📄 `{md_path}`")
    mode = st.radio(
        "View mode", ["Rendered", "Raw"], horizontal=True, label_visibility="collapsed"
    )
    content = md_path.read_text(encoding="utf-8")
    if mode == "Rendered":
        st.markdown(content)
    else:
        st.code(content, language="markdown")
    st.download_button(
        "⬇️ Download .md", content, file_name=md_path.name, mime="text/markdown"
    )
