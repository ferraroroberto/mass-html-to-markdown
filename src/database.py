"""SQLite persistence for parsed comparisons."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .config import load_config, resolve_path
from .logging_utils import get_logger
from .models import FeatureRow, ParsedComparison


logger = get_logger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS comparisons (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    filename       TEXT NOT NULL UNIQUE,
    product_a      TEXT NOT NULL,
    product_b      TEXT NOT NULL,
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    source_hash    TEXT NOT NULL,
    markdown_path  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comparisons_product_a ON comparisons(product_a);
CREATE INDEX IF NOT EXISTS idx_comparisons_product_b ON comparisons(product_b);
CREATE INDEX IF NOT EXISTS idx_comparisons_hash      ON comparisons(source_hash);

CREATE TABLE IF NOT EXISTS features (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    comparison_id       INTEGER NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
    feature_name        TEXT NOT NULL,
    feature_category    TEXT,
    value_a_raw         TEXT,
    value_a_numeric     REAL,
    value_b_raw         TEXT,
    value_b_numeric     REAL,
    winner              TEXT,
    value_a_abbreviated TEXT,
    value_b_abbreviated TEXT
);

CREATE INDEX IF NOT EXISTS idx_features_comparison ON features(comparison_id);
CREATE INDEX IF NOT EXISTS idx_features_name       ON features(feature_name);

CREATE TABLE IF NOT EXISTS products (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    canonical_name TEXT NOT NULL,
    first_seen_at  TEXT NOT NULL
);

-- Second-pass summary cache (issue #20). Each unique source text is summarized
-- exactly once per (word_limit, prompt_version, model); identical text reuses
-- the stored result forever, which is what keeps the abbreviation pass cheap and
-- the rendered Markdown deterministic across re-runs.
CREATE TABLE IF NOT EXISTS text_summaries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    text_hash        TEXT NOT NULL,
    word_limit       INTEGER NOT NULL,
    prompt_version   TEXT NOT NULL,
    model            TEXT NOT NULL,
    original_text    TEXT NOT NULL,
    abbreviated_text TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE(text_hash, word_limit, prompt_version, model)
);
"""


# Columns added after the first release; init_db() migrates existing DBs in place
# rather than requiring a wipe. (table, column, type)
_MIGRATIONS = [
    ("features", "value_a_abbreviated", "TEXT"),
    ("features", "value_b_abbreviated", "TEXT"),
]


def db_path() -> Path:
    cfg = load_config()
    p = resolve_path(cfg["paths"]["database_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Bring pre-#20 databases up to date: CREATE TABLE IF NOT EXISTS leaves
        # an existing `features` table untouched, so add any missing columns.
        for table, column, coltype in _MIGRATIONS:
            if column not in _column_names(conn, table):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                logger.info("Migrated %s: added column %s", table, column)
    logger.info("Database initialized at %s", db_path())


def existing_hash(filename: str) -> Optional[str]:
    with connect() as conn:
        row = conn.execute(
            "SELECT source_hash FROM comparisons WHERE filename = ?", (filename,)
        ).fetchone()
    return row["source_hash"] if row else None


def upsert_comparison(parsed: ParsedComparison, markdown_path: Optional[str]) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        cur = conn.execute(
            "SELECT id FROM comparisons WHERE filename = ?", (parsed.filename,)
        ).fetchone()
        if cur:
            comparison_id = cur["id"]
            conn.execute(
                """UPDATE comparisons
                   SET product_a = ?, product_b = ?, metadata_json = ?,
                       source_hash = ?, markdown_path = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    parsed.product_a,
                    parsed.product_b,
                    json.dumps(parsed.metadata, ensure_ascii=False),
                    parsed.source_hash,
                    markdown_path,
                    now,
                    comparison_id,
                ),
            )
            conn.execute("DELETE FROM features WHERE comparison_id = ?", (comparison_id,))
        else:
            cur2 = conn.execute(
                """INSERT INTO comparisons
                   (filename, product_a, product_b, metadata_json, source_hash,
                    markdown_path, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    parsed.filename,
                    parsed.product_a,
                    parsed.product_b,
                    json.dumps(parsed.metadata, ensure_ascii=False),
                    parsed.source_hash,
                    markdown_path,
                    now,
                    now,
                ),
            )
            comparison_id = cur2.lastrowid

        for f in parsed.features:
            conn.execute(
                """INSERT INTO features
                   (comparison_id, feature_name, feature_category,
                    value_a_raw, value_a_numeric, value_b_raw, value_b_numeric, winner)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    comparison_id,
                    f.name,
                    f.category,
                    f.value_a_raw,
                    f.value_a_numeric,
                    f.value_b_raw,
                    f.value_b_numeric,
                    f.winner,
                ),
            )

        for name in (parsed.product_a, parsed.product_b):
            conn.execute(
                """INSERT INTO products (name, canonical_name, first_seen_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(name) DO NOTHING""",
                (name, name.lower().strip(), now),
            )

    return comparison_id


def list_comparisons() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, filename, product_a, product_b, markdown_path,
                      created_at, updated_at
               FROM comparisons ORDER BY updated_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_comparison(comparison_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM comparisons WHERE id = ?", (comparison_id,)
        ).fetchone()
        if not row:
            return None
        features = conn.execute(
            "SELECT * FROM features WHERE comparison_id = ? ORDER BY id",
            (comparison_id,),
        ).fetchall()
    out = dict(row)
    out["features"] = [dict(f) for f in features]
    return out


def count_features(limit: Optional[int] = None) -> int:
    """Return total feature count, optionally scoped to the most recent *limit* comparisons."""
    with connect() as conn:
        if limit is None:
            row = conn.execute("SELECT COUNT(*) FROM features").fetchone()
        else:
            row = conn.execute(
                """SELECT COUNT(*) FROM features
                   WHERE comparison_id IN (
                       SELECT id FROM comparisons ORDER BY updated_at DESC LIMIT ?
                   )""",
                (limit,),
            ).fetchone()
    return row[0]


def list_products() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT name, canonical_name, first_seen_at FROM products ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Second-pass abbreviation support (issue #20)
# --------------------------------------------------------------------------- #

def text_hash(text: str) -> str:
    """Stable content hash used as the summary-cache key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _word_count(text: Optional[str]) -> int:
    return len(text.split()) if text else 0


def unique_long_values(word_limit: int) -> list[str]:
    """Distinct feature value texts whose word count exceeds *word_limit*.

    Dedup happens here: a blurb repeated across many rows/files appears once, so
    the caller summarizes it a single time and fans the result back out. Returned
    sorted for deterministic processing order.
    """
    with connect() as conn:
        rows = conn.execute(
            """SELECT value_a_raw AS v FROM features WHERE value_a_raw IS NOT NULL
               UNION
               SELECT value_b_raw AS v FROM features WHERE value_b_raw IS NOT NULL"""
        ).fetchall()
    uniques = {r["v"] for r in rows if _word_count(r["v"]) > word_limit}
    return sorted(uniques)


def get_cached_summary(
    text: str, word_limit: int, prompt_version: str, model: str
) -> Optional[str]:
    with connect() as conn:
        row = conn.execute(
            """SELECT abbreviated_text FROM text_summaries
               WHERE text_hash = ? AND word_limit = ? AND prompt_version = ?
                     AND model = ?""",
            (text_hash(text), word_limit, prompt_version, model),
        ).fetchone()
    return row["abbreviated_text"] if row else None


def put_cached_summary(
    text: str, abbreviated: str, word_limit: int, prompt_version: str, model: str
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            """INSERT INTO text_summaries
               (text_hash, word_limit, prompt_version, model,
                original_text, abbreviated_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(text_hash, word_limit, prompt_version, model)
               DO UPDATE SET abbreviated_text = excluded.abbreviated_text,
                             created_at = excluded.created_at""",
            (
                text_hash(text),
                word_limit,
                prompt_version,
                model,
                text,
                abbreviated,
                now,
            ),
        )


def apply_abbreviation(original_text: str, abbreviated_text: str) -> int:
    """Write *abbreviated_text* into every feature cell whose raw value matches
    *original_text* (both A and B columns). Returns the number of cells updated."""
    with connect() as conn:
        cur_a = conn.execute(
            "UPDATE features SET value_a_abbreviated = ? WHERE value_a_raw = ?",
            (abbreviated_text, original_text),
        )
        cur_b = conn.execute(
            "UPDATE features SET value_b_abbreviated = ? WHERE value_b_raw = ?",
            (abbreviated_text, original_text),
        )
        return cur_a.rowcount + cur_b.rowcount


def fill_default_abbreviations() -> int:
    """Copy raw → abbreviated for any cell still unset (the under-limit values),
    so the abbreviated columns are fully populated after a pass. Returns cells set."""
    with connect() as conn:
        cur_a = conn.execute(
            """UPDATE features SET value_a_abbreviated = value_a_raw
               WHERE value_a_abbreviated IS NULL AND value_a_raw IS NOT NULL"""
        )
        cur_b = conn.execute(
            """UPDATE features SET value_b_abbreviated = value_b_raw
               WHERE value_b_abbreviated IS NULL AND value_b_raw IS NOT NULL"""
        )
        return cur_a.rowcount + cur_b.rowcount


def reconstruct_parsed(comparison_id: int) -> Optional[ParsedComparison]:
    """Rebuild a ParsedComparison from the database (including abbreviated values)
    so Markdown can be re-rendered for either variant without re-parsing HTML."""
    row = get_comparison(comparison_id)
    if row is None:
        return None
    features = [
        FeatureRow(
            name=f["feature_name"],
            category=f["feature_category"],
            value_a_raw=f["value_a_raw"],
            value_a_numeric=f["value_a_numeric"],
            value_b_raw=f["value_b_raw"],
            value_b_numeric=f["value_b_numeric"],
            winner=f["winner"],
            value_a_abbreviated=f["value_a_abbreviated"],
            value_b_abbreviated=f["value_b_abbreviated"],
        )
        for f in row["features"]
    ]
    return ParsedComparison(
        filename=row["filename"],
        product_a=row["product_a"],
        product_b=row["product_b"],
        metadata=json.loads(row["metadata_json"]),
        features=features,
        source_hash=row["source_hash"],
    )
