# Product Comparator → RAG Pipeline

Ingest HTML product-comparison pages, persist them to SQLite, and emit
RAG-optimized Markdown for downstream retrieval.

## Layout

```
mass-html-to-markdown/
├── app/                      Streamlit admin (horizontal tabs, thin UI layer)
│   ├── app.py                entrypoint
│   ├── tab_ingest.py
│   ├── tab_browse.py
│   ├── tab_markdown.py
│   └── tab_config.py
├── src/                      business logic (no Streamlit imports)
│   ├── config.py             config.json loader
│   ├── models.py             Pydantic data contracts
│   ├── parser.py             HTML → ParsedComparison  ← edit only for new strategy types; usually write a profile instead
│   ├── database.py           SQLite persistence
│   ├── markdown_gen.py       RAG-optimized Markdown template
│   ├── pipeline.py           orchestrator + CLI
│   └── logging_utils.py
├── data/
│   ├── html/                 input HTML files
│   ├── profiles/             extraction profiles (JSON); one per HTML template
│   │   ├── default.json      standard <table class="comparison"> layout
│   │   └── thead_plain.json  <thead>-based layout (W3Schools / GeeksforGeeks style)
│   ├── markdown/             generated .md files (gitignored)
│   └── db/                   SQLite database (gitignored)
├── tests/
│   ├── sample_data/          bundled HTML fixtures used by the test suite
│   └── test_pipeline.py
├── tmp/.gitkeep
├── config.json.example
├── config.json               gitignored; auto-created from example
├── .env.example
├── requirements.txt
├── launch_app.bat
└── README.md
```

## Quickstart

```bash
# Create the virtual environment
python -m venv .venv

# Install dependencies (invoke via the venv Python directly — do not activate)
# Windows:
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
# POSIX:
./.venv/bin/python -m pip install -r requirements.txt

# Copy config and env files
cp config.json.example config.json
cp .env.example .env          # edit LOG_LEVEL or API keys as needed

# 1. Run the pipeline on the bundled sample data (3 files)
# Windows:
& .\.venv\Scripts\python.exe -m src.pipeline ingest
# POSIX:
./.venv/bin/python -m src.pipeline ingest

# 2. Inspect the generated Markdown
ls data/markdown/

# 3. Launch the admin UI
# Windows: launch_app.bat
#   POSIX: ./.venv/bin/python -m streamlit run app/app.py
```

### Environment

Copy `.env.example` to `.env` (gitignored) before the first run. Supported variables:

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `ANTHROPIC_API_KEY` | *(empty)* | Optional — only needed if you wire an LLM step into the pipeline |
| `OPENAI_API_KEY` | *(empty)* | Optional — only needed if you wire an LLM step into the pipeline |

## Adapting to your real HTML

The parser is **profile-driven**: extraction rules live in JSON files under
`data/profiles/`, not in Python. One profile = one HTML template. To target a
new template, write a new profile; no code changes.

### Profile anatomy

```json
{
  "name": "default",
  "product_names": {
    "product_a": [
      { "type": "attr", "selector": "h1", "attr": "data-product-a" },
      { "type": "filename_group", "group": "product_a" }
    ],
    "product_b": [...]
  },
  "metadata": {
    "category": { "type": "attr", "selector": "meta[name='category']", "attr": "content" }
  },
  "table": {
    "selector":              "table.comparison",
    "row_selector":          "tbody tr",
    "feature_cell":          "td.feature",
    "feature_category_attr": "data-category",
    "value_a_cell":          "td.value-a",
    "value_b_cell":          "td.value-b"
  },
  "value_normalizers": [
    { "pattern": "^\\s*(✓|✔|yes|true)\\s*$",   "flags": "i", "replace": "yes" },
    { "pattern": "^\\s*(✗|❌|no|false)\\s*$",  "flags": "i", "replace": "no"  }
  ]
}
```

- **`product_names`** — ordered strategies; first non-empty hit wins. Supported types:
  `attr` (CSS selector + attribute), `text` (CSS selector + inner text),
  `filename_group` (regex group from `ingestion.filename_pattern`).
- **`metadata`** — optional key-value extractions for YAML frontmatter.
- **`table`** — CSS selectors that locate the comparison table and its cells.
- **`value_normalizers`** — ordered regex rules; the first match replaces the raw
  value (useful for ✓/✗ icons, "n/a", etc.).

### Bundled profiles

| Profile | Target structure |
|---|---|
| `data/profiles/default.json` | `<table class="comparison">` with `td.feature`/`td.value-a`/`td.value-b` |
| `data/profiles/thead_plain.json` | `<thead>` holds product names as `<th>`; feature name is first `<td>` of each row (W3Schools / GeeksforGeeks style) |

### Steps to target your real HTML

1. Drop your files in `data/html/` and name them consistently (update
   `ingestion.filename_pattern` in `config.json`).
2. Open one file in a browser, use DevTools to find the CSS selectors for the
   table, feature cell, and value cells.
3. Copy `data/profiles/default.json` to `data/profiles/mysite.json` and edit
   the selectors.
4. Point `ingestion.profile` at it in `config.json` — or pass
   `--profile data/profiles/mysite.json` on the CLI, or pick it from the
   selectbox in the Ingest tab.
5. Run `python -m src.pipeline ingest --force` and check `data/markdown/` +
   the Browse tab.
6. If anything looks off, add a test against your sample in `tests/`.

### Multiple sources

If you later ingest from a **second source** with a different template, just
add a second profile and run two ingestion passes (one per profile). Both sets
of comparisons land in the same database and same Markdown folder.

## Editing the parser in Python

You only need to touch `src/parser.py` when:

- You need a new **strategy type** (e.g. JSON-LD extraction, regex-over-text).
- You need a new **normalizer** more complex than regex substitution.
- The HTML structure truly doesn't fit the "one table, one row per feature" model
  — e.g. the CodyHouse/Sony transposed grid, where features and products live in
  separate containers. In that case, add a new table-strategy type to the parser
  and a new profile key to select it.


## Markdown structure (why it's shaped this way)

Every `.md` has:

- **YAML frontmatter** (`product_a`, `product_b`, `category`, `source_file`) so the
  vector store can metadata-filter before semantic search.
- **At-a-glance table** — a dense chunk that answers broad questions in one retrieval.
- **Feature-by-feature H3 sections** — narrow chunks that answer targeted questions.
- **Search keywords footer** — catches lexical variants of the same query.

Chunk at H2 for the retriever, with H3 as overlap.

## Design principles

- `app/` is a thin UI layer; all logic lives in `src/` and is independently callable
  (CLI, tests, notebooks).
- Pydantic contracts separate parser → DB → Markdown, so each layer can be swapped
  without touching the others.
- Ingestion is **idempotent** (content-hash check); safe to re-run.
- One bad HTML file does not abort a 200-file batch.
- Deterministic Markdown output (same HTML → byte-identical `.md`).
