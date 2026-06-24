"""Summarize tab — second pass: LLM-abbreviate long feature values (issue #20).

Thin UI over ``src.summarizer`` and ``src.pipeline``. All work happens in src/.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.pipeline import render_variant
from src.summarizer import (
    BACKENDS,
    build_prompt,
    default_model,
    run_abbreviation_pass,
    summarization_config,
)


def render_summarize_tab() -> None:
    cfg = summarization_config()

    st.subheader("Second pass — abbreviate feature values")
    st.caption(
        "Shorten verbose feature text in the database, then render a `short/` "
        "Markdown variant with the same structure. Identical text is summarized "
        "once and cached, so re-runs are free and output stays deterministic."
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        word_limit = st.number_input(
            "Word limit",
            min_value=5,
            max_value=500,
            value=int(cfg["word_limit"]),
            step=5,
            help="Values longer than this (in words) get summarized.",
            key="sm_word_limit",
        )
    with col2:
        backend = st.selectbox(
            "Backend",
            options=list(BACKENDS),
            index=list(BACKENDS).index(cfg["backend"]) if cfg["backend"] in BACKENDS else 1,
            help="gemini = production · local-hub = local LLM hub · fake = offline truncation",
            key="sm_backend",
        )
    with col3:
        model = st.text_input(
            "Model",
            value=default_model(backend),
            help="Override the backend's default model id.",
            key="sm_model",
        )

    with st.expander("Prompt sent to the LLM (editable)", expanded=False):
        prompt = st.text_area(
            "Prompt",
            value=build_prompt(int(word_limit)),
            height=160,
            label_visibility="collapsed",
            key="sm_prompt",
        )

    st.divider()

    pcol, rcol = st.columns([1, 1])
    with pcol:
        if st.button("🔍 Dry run (count only)", width="stretch", key="sm_dryrun"):
            stats = run_abbreviation_pass(
                word_limit=int(word_limit),
                backend=backend,
                model=model,
                prompt=prompt,
                dry_run=True,
            )
            m1, m2, m3 = st.columns(3)
            m1.metric("Unique over-limit texts", stats.unique_long)
            m2.metric("LLM calls needed", stats.llm_calls)
            m3.metric("Already cached", stats.cache_hits)
            if stats.unique_long == 0:
                st.success("Nothing exceeds the word limit — no LLM calls needed.")
            else:
                st.info(
                    f"Running will make **{stats.llm_calls}** LLM call(s) "
                    f"({stats.cache_hits} reused from cache)."
                )

    with rcol:
        run = st.button("✂️ Run abbreviation pass", type="primary", width="stretch", key="sm_run")

    if run:
        progress_bar = st.progress(0.0, text="Starting…")

        def on_progress(idx: int, total: int, snippet: str) -> None:
            progress_bar.progress(idx / max(total, 1), text=f"{idx}/{total} — {snippet}…")

        stats = run_abbreviation_pass(
            word_limit=int(word_limit),
            backend=backend,
            model=model,
            prompt=prompt,
            dry_run=False,
            progress=on_progress,
        )
        progress_bar.empty()

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Unique over-limit", stats.unique_long)
        m2.metric("LLM calls", stats.llm_calls)
        m3.metric("Cache hits", stats.cache_hits)
        m4.metric("Cells updated", stats.cells_updated)

        if stats.errors:
            st.error(f"{len(stats.errors)} value(s) failed to summarize:")
            st.dataframe(pd.DataFrame({"error": stats.errors}), width="stretch", hide_index=True)
        else:
            st.success("Abbreviation pass complete.")

        # Re-render the short variant from the DB and validate the skeleton.
        try:
            paths = render_variant(variant="short", validate=True)
            st.success(f"Rendered {len(paths)} short Markdown file(s) to `data/markdown/short/`.")
        except ValueError as exc:
            st.error(f"Structure validation failed — short variant diverged:\n\n{exc}")
