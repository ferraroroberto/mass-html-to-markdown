"""Config tab — view and edit config.json."""

from __future__ import annotations

import json

import streamlit as st

from src.config import load_config, save_config


def render_config_tab() -> None:
    cfg = load_config()
    st.caption("Edit `config.json`. Changes persist to disk.")

    text = st.text_area(
        "config.json",
        value=json.dumps(cfg, indent=2),
        height=400,
        label_visibility="collapsed",
        key="config_textarea",
    )

    col1, _ = st.columns([1, 4])
    if col1.button("💾 Save", type="primary", key="config_save"):
        try:
            new_cfg = json.loads(text)
            save_config(new_cfg)
            st.success("Saved. Reload the app for all modules to pick it up.")
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")

    st.divider()
    st.write("**Current paths (resolved)**")
    from src.config import resolve_path  # local import to avoid cache staleness

    for key, rel in cfg["paths"].items():
        st.code(f"{key}: {resolve_path(rel)}", language="text")
