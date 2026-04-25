"""Ingest tab — run the pipeline over a folder of HTML files."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from src.config import load_config, resolve_path
from src.parser import active_profile_path, list_profiles
from src.pipeline import ingest_folder


def render_ingest_tab() -> None:
    cfg = load_config()

    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        input_dir = st.text_input(
            "HTML input folder",
            value=cfg["paths"]["html_input_dir"],
            help="Folder containing the *.html comparison pages.",
        )
    with col2:
        force = st.checkbox("Force re-ingest", value=False)
    with col3:
        limit_str = st.text_input("Limit", value="", placeholder="all")

    profiles = list_profiles()
    active = active_profile_path()
    if profiles:
        labels = {str(p): p.stem for p in profiles}
        default_idx = next(
            (i for i, p in enumerate(profiles) if p.resolve() == active.resolve()), 0
        )
        picked = st.selectbox(
            "Parser profile",
            options=[str(p) for p in profiles],
            index=default_idx,
            format_func=lambda s: labels[s],
            help="Swap in a different profile to parse a different HTML template.",
        )
        profile_override = Path(picked) if picked != str(active) else None
    else:
        profile_override = None
        st.caption(f"Active profile: `{active}`")

    input_path = resolve_path(input_dir)
    files = sorted(input_path.glob("*.html")) if input_path.exists() else []
    st.info(f"Found **{len(files)}** HTML files in `{input_path}`")

    if st.button("🚀 Run ingestion", type="primary", disabled=not files):
        limit = int(limit_str) if limit_str.strip().isdigit() else None

        progress_bar = st.progress(0.0, text="Starting...")
        status_placeholder = st.empty()

        def on_progress(idx: int, total: int, fname: str) -> None:
            progress_bar.progress(idx / max(total, 1), text=f"{idx}/{total} — {fname}")

        results = ingest_folder(
            input_path,
            force=force,
            limit=limit,
            progress=on_progress,
            profile=profile_override,
        )
        progress_bar.empty()

        df = pd.DataFrame([r.__dict__ for r in results])
        ok = (df["status"] == "parsed").sum()
        skipped = (df["status"] == "skipped").sum()
        errors = (df["status"] == "error").sum()

        m1, m2, m3 = st.columns(3)
        m1.metric("Parsed", ok)
        m2.metric("Skipped", skipped)
        m3.metric("Errors", errors)
        status_placeholder.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    with st.expander("Sample file list"):
        st.write([f.name for f in files[:25]])
        if len(files) > 25:
            st.caption(f"... and {len(files) - 25} more")
