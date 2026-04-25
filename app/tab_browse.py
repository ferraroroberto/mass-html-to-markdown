"""Browse tab — list comparisons and drill down."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.database import get_comparison, list_comparisons, list_products


def render_browse_tab() -> None:
    comparisons = list_comparisons()
    products = list_products()

    if not comparisons:
        st.info("No comparisons ingested yet. Go to the Ingest tab first.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Comparisons", len(comparisons))
    c2.metric("Unique products", len(products))
    total_features = sum(
        len(get_comparison(c["id"])["features"]) for c in comparisons[:50]
    )
    c3.metric("Features (first 50)", total_features)

    st.subheader("Comparisons")
    query = st.text_input("Filter by product name", "")
    df = pd.DataFrame(comparisons)
    if query:
        q = query.lower()
        df = df[
            df["product_a"].str.lower().str.contains(q)
            | df["product_b"].str.lower().str.contains(q)
            | df["filename"].str.lower().str.contains(q)
        ]
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("Drill into a comparison")
    ids = [c["id"] for c in comparisons]
    labels = {
        c["id"]: f"#{c['id']} — {c['product_a']} vs {c['product_b']}"
        for c in comparisons
    }
    picked = st.selectbox(
        "Select", options=ids, format_func=lambda i: labels[i]
    )
    if picked:
        detail = get_comparison(picked)
        if detail:
            meta_col, feat_col = st.columns([1, 2])
            with meta_col:
                st.write("**Metadata**")
                st.json(
                    {k: v for k, v in detail.items() if k != "features"}
                )
            with feat_col:
                st.write(f"**Features ({len(detail['features'])})**")
                st.dataframe(
                    pd.DataFrame(detail["features"]),
                    use_container_width=True,
                    hide_index=True,
                )
