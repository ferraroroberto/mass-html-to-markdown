"""Streamlit admin for the product comparator.

All business logic lives in src/. This file only wires the UI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Make `src` importable when running via `streamlit run app/app.py`
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config  # noqa: E402

from tab_ingest import render_ingest_tab  # noqa: E402
from tab_browse import render_browse_tab  # noqa: E402
from tab_markdown import render_markdown_tab  # noqa: E402
from tab_config import render_config_tab  # noqa: E402


def main() -> None:
    cfg = load_config()
    st.set_page_config(
        page_title=cfg["app"]["title"],
        page_icon="📊",
        layout="wide",
    )
    st.title(cfg["app"]["title"])
    st.caption(
        "Ingest HTML product comparisons → SQLite + RAG-ready Markdown."
    )

    tab_ing, tab_brw, tab_md, tab_cfg = st.tabs(
        ["📥 Ingest", "🔎 Browse", "📝 Markdown preview", "⚙️ Config"]
    )
    with tab_ing:
        render_ingest_tab()
    with tab_brw:
        render_browse_tab()
    with tab_md:
        render_markdown_tab()
    with tab_cfg:
        render_config_tab()


if __name__ == "__main__":
    main()
