"""RAG-optimized Markdown generation."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .config import load_config, resolve_path
from .models import ParsedComparison


def render_markdown(parsed: ParsedComparison) -> str:
    """Render a comparison into Markdown optimized for retrieval."""
    cfg = load_config()
    md_cfg = cfg.get("markdown", {})

    lines: list[str] = []

    # --- Frontmatter (metadata filtering in the vector store) -------------- #
    lines.append("---")
    lines.append("type: product_comparison")
    lines.append(f'product_a: "{_escape(parsed.product_a)}"')
    lines.append(f'product_b: "{_escape(parsed.product_b)}"')
    lines.append(
        f'products: ["{_escape(parsed.product_a)}", "{_escape(parsed.product_b)}"]'
    )
    if parsed.metadata.get("category"):
        lines.append(f'category: "{_escape(parsed.metadata["category"])}"')
    lines.append(f'source_file: "{parsed.filename}"')
    # NOTE: deliberately no `ingested_at` here. A wall-clock timestamp in the
    # rendered body would break the "same HTML -> byte-identical .md" invariant
    # (README "Design principles") and make --force re-ingests rewrite every
    # file with a new value. Ingestion timing is provenance that already lives
    # in the database (comparisons.created_at / updated_at), which is the right
    # home for mutable metadata; `source_file` covers origin provenance here.
    lines.append("---")
    lines.append("")

    # --- Title ------------------------------------------------------------ #
    lines.append(f"# {parsed.product_a} vs {parsed.product_b}")
    lines.append("")

    # --- Overview --------------------------------------------------------- #
    if md_cfg.get("include_overview_paragraph", True):
        lines.append("## Overview")
        cat = parsed.metadata.get("category")
        cat_txt = f" in the {cat} category" if cat else ""
        lines.append(
            f"This document compares **{parsed.product_a}** and "
            f"**{parsed.product_b}**{cat_txt} across "
            f"{len(parsed.features)} features."
        )
        lines.append("")

    # --- At-a-glance table (dense context for broad queries) -------------- #
    lines.append("## At a glance")
    lines.append(f"| Attribute | {parsed.product_a} | {parsed.product_b} |")
    lines.append("|---|---|---|")
    for f in parsed.features:
        lines.append(
            f"| {_cell(f.name)} | {_cell(f.value_a_raw)} | {_cell(f.value_b_raw)} |"
        )
    lines.append("")

    # --- Feature-by-feature (narrow chunks for targeted queries) ---------- #
    lines.append("## Feature-by-feature analysis")
    lines.append("")
    grouped: dict[str, list] = defaultdict(list)
    for f in parsed.features:
        grouped[f.category or "General"].append(f)
    for category, rows in grouped.items():
        lines.append(f"### {category}")
        lines.append("")
        for f in rows:
            lines.append(f"#### {f.name}")
            lines.append(f"- **{parsed.product_a}**: {f.value_a_raw or 'n/a'}")
            lines.append(f"- **{parsed.product_b}**: {f.value_b_raw or 'n/a'}")
            if f.winner == "A":
                lines.append(f"- **Edge**: {parsed.product_a}")
            elif f.winner == "B":
                lines.append(f"- **Edge**: {parsed.product_b}")
            elif f.winner == "tie":
                lines.append("- **Edge**: tie")
            lines.append("")

    # --- Search keywords footer ------------------------------------------- #
    if md_cfg.get("include_search_keywords", True):
        kws = {
            parsed.product_a,
            parsed.product_b,
            "comparison",
            "versus",
            "vs",
            "compare",
        }
        if parsed.metadata.get("category"):
            kws.add(parsed.metadata["category"])
        for f in parsed.features:
            kws.add(f.name)
        lines.append("## Search keywords")
        lines.append(", ".join(sorted(kws)))
        lines.append("")

    return "\n".join(lines)


def write_markdown(parsed: ParsedComparison) -> Path:
    """Write the Markdown file to disk and return the path."""
    cfg = load_config()
    out_dir = resolve_path(cfg["paths"]["markdown_output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(parsed.filename).stem
    out_path = out_dir / f"{stem}.md"
    out_path.write_text(render_markdown(parsed), encoding="utf-8")
    return out_path


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _escape(text: str) -> str:
    return text.replace('"', '\\"')


def _cell(text) -> str:
    if text is None:
        return ""
    return str(text).replace("|", "\\|").replace("\n", " ")
