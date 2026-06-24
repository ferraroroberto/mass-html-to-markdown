"""Markdown preview tab — inspect the RAG output."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.database import list_comparisons
from src.markdown_gen import variant_dir


def render_markdown_tab() -> None:
    comparisons = list_comparisons()
    if not comparisons:
        st.info("No comparisons ingested yet.")
        return

    labels = {
        c["id"]: f"#{c['id']} — {c['product_a']} vs {c['product_b']}"
        for c in comparisons
    }
    top = st.columns([3, 1])
    with top[0]:
        picked = st.selectbox(
            "Select a comparison",
            options=[c["id"] for c in comparisons],
            format_func=lambda i: labels[i],
            key="md_select",
        )
    with top[1]:
        variant = st.radio(
            "Variant", ["full", "short"], horizontal=True, key="md_variant"
        )
    row = next(c for c in comparisons if c["id"] == picked)
    filename = row.get("filename")
    if not filename:
        st.warning("No source filename on record for this comparison.")
        return

    md_path = variant_dir(variant) / f"{Path(filename).stem}.md"
    if not md_path.exists():
        if variant == "short":
            st.info("No short variant yet — run the abbreviation pass in the ✂️ Summarize tab.")
        else:
            st.error(f"Markdown file missing at {md_path}")
        return

    st.caption(f"📄 `{md_path}`")
    mode = st.radio(
        "View mode", ["Rendered", "Raw"], horizontal=True, label_visibility="collapsed", key="md_viewmode"
    )
    content = md_path.read_text(encoding="utf-8")
    if mode == "Rendered":
        st.markdown(content)
    else:
        st.code(content, language="markdown")
    st.download_button(
        "⬇️ Download .md", content, file_name=md_path.name, mime="text/markdown", key="md_download"
    )
