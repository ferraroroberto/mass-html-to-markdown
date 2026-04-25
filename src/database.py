"""SQLite persistence for parsed comparisons."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .config import load_config, resolve_path
from .logging_utils import get_logger
from .models import ParsedComparison


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
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    comparison_id    INTEGER NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
    feature_name     TEXT NOT NULL,
    feature_category TEXT,
    value_a_raw      TEXT,
    value_a_numeric  REAL,
    value_b_raw      TEXT,
    value_b_numeric  REAL,
    winner           TEXT
);

CREATE INDEX IF NOT EXISTS idx_features_comparison ON features(comparison_id);
CREATE INDEX IF NOT EXISTS idx_features_name       ON features(feature_name);

CREATE TABLE IF NOT EXISTS products (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    canonical_name TEXT NOT NULL,
    first_seen_at  TEXT NOT NULL
);
"""


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


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
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


def list_products() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT name, canonical_name, first_seen_at FROM products ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]
