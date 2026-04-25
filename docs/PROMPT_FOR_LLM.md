# Master Prompt: HTML Product Comparator → Database + Markdown for RAG

> Use this prompt with a capable LLM (e.g. Claude, GPT-4 class) to generate the full production implementation. The scaffolding you already have is the structural blueprint; this prompt tells the LLM how to replace the sample parser and sample data with the real thing.

---

## Role

You are a senior Python engineer. You will extend an existing scaffolded project that ingests HTML product-comparison pages, persists their content to a SQLite database, and emits RAG-optimized Markdown documents. A Streamlit admin UI already exists. Your job is to (a) replace the sample HTML parser with a real one that matches the user's HTML structure, (b) refine the database schema if needed, (c) refine the Markdown template for best RAG retrieval, and (d) add tests.

## Context

- The user has **100–200 HTML files** in `data/html/`.
- Each file compares **Product A vs Product B**. File names encode the products, e.g. `ProductA_vs_ProductB.html` or similar — the exact convention will be specified by the user.
- The HTML structure is consistent across files (same template rendered with different data).
- The output will feed a **RAG agent** that answers user questions like *"How does Product X compare with Product Y on feature Z?"* or *"Which products are cheaper than Product X?"*.
- The scaffolding follows the user's standard conventions: `config.json` (+ `config.json.example`), `.env`, `/src` for logic, `/app` for Streamlit with horizontal tabs, `launch_app.bat`, `/tmp` with `.gitkeep`, `README.md`, `requirements.txt`.

## Inputs the user will give you

1. **One or two real HTML files** as ground-truth examples.
2. **A screenshot** of the rendered page so you understand the visual structure.
3. **The naming convention** for files (how product names are encoded).
4. **A description of the target RAG queries** the agent must answer.

Before writing any code, **read the sample HTML carefully** and identify:
- The CSS selectors / tag patterns that locate the product names.
- The selectors that locate the feature rows (name, value-A, value-B).
- Any metadata worth extracting (category, last updated, source URL, price, rating, etc.).
- Edge cases: missing values, merged cells, nested tables, icons standing in for yes/no.

## Deliverables

### 1. `src/parser.py` — real HTML parser

- Use `BeautifulSoup` (lxml parser).
- Export a single public function: `parse_html(html_path: Path) -> ParsedComparison`.
- `ParsedComparison` is a Pydantic model (already defined in `src/models.py`) with: `product_a`, `product_b`, `metadata: dict`, `features: list[FeatureRow]`.
- Fallback cleanly when a selector misses: log a warning, set the value to `None`, never crash the batch.
- Extract product names from **both filename and HTML title/body**, and verify they agree. Emit a warning if they don't.
- Normalize feature names (strip, collapse whitespace, title-case if appropriate).
- Preserve raw values **and** produce a normalized form (e.g. `"$1,299.00"` → `1299.00` in a separate `value_numeric` field) where feasible.

### 2. `src/database.py` — schema refinement

Keep the existing 3-table schema unless the HTML demands more:

- `comparisons(id, filename, product_a, product_b, metadata_json, source_hash, created_at, markdown_path)`
- `features(id, comparison_id, feature_name, feature_category, value_a_raw, value_a_numeric, value_b_raw, value_b_numeric, winner)`
- `products(id, name, canonical_name, first_seen_at)` — populated via upsert from every ingested comparison.

Add indexes on `product_a`, `product_b`, `feature_name`, and `comparisons.source_hash` (for idempotent re-ingest).

### 3. `src/markdown_gen.py` — RAG-optimized Markdown

Generate one `.md` file per comparison with this structure (critical for retrieval quality):

```markdown
---
type: product_comparison
product_a: "<Product A>"
product_b: "<Product B>"
products: ["<Product A>", "<Product B>"]
category: "<category if known>"
source_file: "<original filename>"
ingested_at: "<ISO timestamp>"
---

# <Product A> vs <Product B>

## Overview
<One paragraph summarizing what's being compared and any headline metadata>

## At a Glance
| Attribute | <Product A> | <Product B> |
|---|---|---|
| <feature 1> | <value A> | <value B> |
| ... | ... | ... |

## Feature-by-feature analysis
### <Feature name>
- **<Product A>**: <value>
- **<Product B>**: <value>
- **Winner / notes**: <derived insight if reliable, otherwise omit>

<...repeat per feature...>

## Search keywords
<Product A>, <Product B>, <category>, <key features>, comparison, versus, vs, compare
```

Rationale:
- **Frontmatter** enables metadata filtering in the vector store.
- The **"At a Glance" table** gives dense context so a single retrieved chunk answers broad questions.
- **Per-feature H3 sections** give narrow chunks for specific queries.
- **Search keywords footer** catches queries that phrase things differently.

Chunking guidance (for the RAG builder downstream): chunk at H2 level with H3 as overlap.

### 4. Idempotent ingestion

- Hash the source HTML content; skip re-ingest when `source_hash` matches an existing row, unless `--force` is passed.
- On `--force`, replace the comparison and its features in one transaction, re-emit the Markdown.

### 5. CLI

`python -m src.pipeline ingest --input data/html --output data/markdown [--force] [--limit N]`

### 6. Tests (`tests/`)

- `test_parser.py` — assert the parser extracts expected fields from the user's real sample HTML.
- `test_database.py` — round-trip insert/read, idempotent re-ingest.
- `test_markdown_gen.py` — snapshot test of the generated MD.
- Use `pytest`.

### 7. Streamlit app refinements (`app/`)

The scaffolding already provides four horizontal tabs: **Ingest**, **Browse**, **Markdown preview**, **Config**. Extend them only if needed:
- Ingest: file picker or folder path, run button, progress bar, per-file status table, error log.
- Browse: filterable table of comparisons + drill-down to feature rows.
- Markdown preview: pick a comparison, show rendered MD + raw MD toggle.
- Config: view current `config.json`, edit via form, save (writes to disk).

### 8. Logging & observability

- Use Python `logging` with a rotating file handler under `/tmp/logs/`.
- Every ingestion run writes a summary: files processed, parsed OK, warnings, errors, duration.

## Non-negotiable best practices

- **Type hints everywhere.**
- **Pydantic** for data contracts between parser → DB → Markdown.
- **No hardcoded paths** — read from `config.json`.
- **No secrets in code** — `.env` only.
- **No business logic in `app/`** — Streamlit imports from `src/`.
- **Deterministic output** — same input HTML always produces the same MD byte-for-byte.
- **Graceful degradation** — one broken HTML must not abort the batch.

## Acceptance checklist

- [ ] Parser tested against the user's real HTML sample and extracts every field visible in the screenshot.
- [ ] 100–200 files ingested in under 2 minutes on a laptop.
- [ ] `sqlite3 data/db/comparator.db ".schema"` shows the expected schema with indexes.
- [ ] Each Markdown file opens cleanly in a preview tool and contains valid frontmatter.
- [ ] Re-running ingestion is a no-op (idempotent).
- [ ] Streamlit app starts with `launch_app.bat` and all four tabs work.
- [ ] `pytest` passes.
- [ ] `README.md` documents setup, ingestion, and how to plug the output into a RAG pipeline.

## Starting point

You are being handed a working scaffolding with sample HTML and a sample parser. Replace `tests/sample_data/` with the user's real HTML, replace `src/parser.py` with a real implementation matching the user's structure, and iterate from there. Do not rewrite the scaffolding from scratch — extend it.
