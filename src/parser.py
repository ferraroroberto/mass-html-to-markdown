"""HTML parser driven by a JSON profile.

A **profile** is a JSON file describing how to extract a comparison from
one HTML template. It lives in ``data/profiles/<name>.json`` and contains:

- ``product_names``: ordered strategies for resolving product A and B (first hit wins)
- ``metadata``:      optional key -> {selector, type, attr} map
- ``table``:         selectors for the table, rows, feature cell, value cells
- ``value_normalizers``: regex-based normalizations (e.g. ``✓`` -> "yes")

Which profile is active:

1. CLI flag ``--profile PATH`` (overrides everything)
2. ``ingestion.profile`` in ``config.json``
3. Fallback: ``data/profiles/default.json``

The public contract ``parse_html(path) -> ParsedComparison`` is unchanged,
so the database, Markdown, and Streamlit layers keep working.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

from .config import load_config, resolve_path
from .logging_utils import get_logger
from .models import FeatureRow, ParsedComparison


logger = get_logger(__name__)

# Module-level override set by the CLI. None -> fall back to config.json.
_PROFILE_OVERRIDE: Optional[Path] = None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def parse_html(html_path: Path) -> ParsedComparison:
    """Parse one HTML file using the active profile."""
    profile = _load_profile()
    raw = html_path.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    soup = BeautifulSoup(raw, "lxml")

    product_a = _resolve_product(
        soup, html_path, profile["product_names"]["product_a"]
    ) or "Unknown A"
    product_b = _resolve_product(
        soup, html_path, profile["product_names"]["product_b"]
    ) or "Unknown B"

    metadata = _extract_metadata(soup, profile.get("metadata", {}))
    features = _extract_features(
        soup, profile["table"], profile.get("value_normalizers", [])
    )

    return ParsedComparison(
        filename=html_path.name,
        product_a=product_a,
        product_b=product_b,
        metadata=metadata,
        features=features,
        source_hash=source_hash,
    )


def set_profile_override(profile_path: Optional[Path]) -> None:
    """Set or clear the module-level profile override (used by the CLI/UI)."""
    global _PROFILE_OVERRIDE
    _PROFILE_OVERRIDE = Path(profile_path) if profile_path else None
    _load_profile.cache_clear()


def active_profile_path() -> Path:
    """Resolve the currently-active profile path."""
    if _PROFILE_OVERRIDE is not None:
        return _PROFILE_OVERRIDE
    cfg = load_config()
    return resolve_path(
        cfg.get("ingestion", {}).get("profile", "data/profiles/default.json")
    )


def list_profiles() -> list[Path]:
    """Discover all profile files under data/profiles/."""
    profiles_dir = resolve_path("data/profiles")
    if not profiles_dir.exists():
        return []
    return sorted(profiles_dir.glob("*.json"))


# --------------------------------------------------------------------------- #
# Profile loading
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=8)
def _load_profile() -> dict:
    path = active_profile_path()
    if not path.exists():
        raise FileNotFoundError(f"Profile not found: {path}")
    profile = json.loads(path.read_text(encoding="utf-8"))
    logger.info("Using parser profile: %s (%s)", profile.get("name", "?"), path)
    return profile


# --------------------------------------------------------------------------- #
# Strategy executors
# --------------------------------------------------------------------------- #

def _resolve_product(
    soup: BeautifulSoup, path: Path, strategies: list[dict]
) -> Optional[str]:
    """Try strategies in order; return the first non-empty result."""
    cfg = load_config()
    for strat in strategies:
        try:
            value = _apply_strategy(strat, soup, path, cfg)
            if value:
                return _clean(value)
        except Exception as exc:  # noqa: BLE001 — never crash on a broken selector
            logger.warning(
                "Product strategy %r failed on %s: %s", strat, path.name, exc
            )
    return None


def _apply_strategy(
    strat: dict, soup: BeautifulSoup, path: Path, cfg: dict
) -> Optional[str]:
    t = strat.get("type")
    if t == "attr":
        el = soup.select_one(strat["selector"])
        return el.get(strat["attr"]) if el else None
    if t == "text":
        el = soup.select_one(strat["selector"])
        return el.get_text() if el else None
    if t == "filename_group":
        pattern = cfg["ingestion"]["filename_pattern"]
        m = re.match(pattern, path.name)
        if not m:
            return None
        return m.group(strat["group"]).replace("-", " ")
    logger.warning("Unknown strategy type: %r", t)
    return None


def _extract_metadata(soup: BeautifulSoup, meta_spec: dict) -> dict:
    out: dict = {}
    for key, spec in meta_spec.items():
        try:
            el = soup.select_one(spec["selector"])
            if el is None:
                continue
            t = spec.get("type", "text")
            val = el.get(spec["attr"]) if t == "attr" else el.get_text()
            if val:
                out[key] = _clean(val)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Metadata %r failed: %s", key, exc)
    return out


def _extract_features(
    soup: BeautifulSoup, table_spec: dict, normalizers: list[dict]
) -> list[FeatureRow]:
    rows: list[FeatureRow] = []

    tables = soup.select(table_spec["selector"])
    if not tables:
        logger.warning("No tables matched selector %r", table_spec["selector"])
        return rows

    cat_attr = table_spec.get("feature_category_attr")
    for table in tables:
        for tr in table.select(table_spec["row_selector"]):
            feat = tr.select_one(table_spec["feature_cell"])
            a = tr.select_one(table_spec["value_a_cell"])
            b = tr.select_one(table_spec["value_b_cell"])
            if not (feat and a and b):
                continue

            name = _clean(feat.get_text())
            if not name:
                continue

            category = feat.get(cat_attr) if cat_attr else None
            raw_a = _apply_normalizers(_clean(a.get_text()), normalizers)
            raw_b = _apply_normalizers(_clean(b.get_text()), normalizers)

            rows.append(
                FeatureRow(
                    name=name,
                    category=category,
                    value_a_raw=raw_a,
                    value_a_numeric=_to_numeric(raw_a),
                    value_b_raw=raw_b,
                    value_b_numeric=_to_numeric(raw_b),
                    winner=_pick_winner(raw_a, raw_b),
                )
            )

    if not rows:
        logger.warning("No feature rows extracted — check the profile selectors")
    return rows


# --------------------------------------------------------------------------- #
# Value-level helpers
# --------------------------------------------------------------------------- #

def _apply_normalizers(value: str, normalizers: list[dict]) -> str:
    """Apply the first matching regex normalization (e.g. ✓ -> 'yes')."""
    for norm in normalizers:
        flags = re.IGNORECASE if "i" in norm.get("flags", "") else 0
        try:
            if re.match(norm["pattern"], value, flags):
                return norm["replace"]
        except re.error as exc:
            logger.warning("Bad normalizer regex %r: %s", norm.get("pattern"), exc)
    return value


_WS = re.compile(r"\s+")
_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _to_numeric(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    m = _NUM.search(value.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _pick_winner(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """Numeric-only heuristic; returns None when values are not comparable."""
    na, nb = _to_numeric(a), _to_numeric(b)
    if na is None or nb is None:
        return None
    if na == nb:
        return "tie"
    return "A" if na > nb else "B"
