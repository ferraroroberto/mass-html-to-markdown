"""Smoke tests proving the pipeline works end-to-end with sample data."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.markdown_gen import render_markdown
from src.parser import parse_html, set_profile_override


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = PROJECT_ROOT / "data" / "html" / "AlphaPro_vs_BetaMax.html"
ALT_SAMPLE = PROJECT_ROOT / "tests" / "sample_data" / "CharlieOne_vs_DeltaPlus.html"
DEFAULT_PROFILE = PROJECT_ROOT / "data" / "profiles" / "default.json"
ALT_PROFILE = PROJECT_ROOT / "data" / "profiles" / "thead_plain.json"


@pytest.fixture(autouse=True)
def reset_profile():
    set_profile_override(None)
    yield
    set_profile_override(None)


# --- Default profile against standard sample --- #

def test_parser_extracts_products():
    parsed = parse_html(SAMPLE)
    assert parsed.product_a == "Alpha Pro"
    assert parsed.product_b == "Beta Max"


def test_parser_extracts_features():
    parsed = parse_html(SAMPLE)
    assert len(parsed.features) == 7


def test_parser_normalizes_numerics():
    parsed = parse_html(SAMPLE)
    price = next(f for f in parsed.features if "Price" in f.name)
    assert price.value_a_numeric == 1299.0
    assert price.value_b_numeric == 1499.0


def test_parser_picks_winner():
    parsed = parse_html(SAMPLE)
    battery = next(f for f in parsed.features if "Battery" in f.name)
    assert battery.winner == "A"


def test_markdown_contains_frontmatter_and_table():
    parsed = parse_html(SAMPLE)
    md = render_markdown(parsed)
    assert md.startswith("---")
    assert "product_a:" in md
    assert "## At a glance" in md


# --- Alt profile against a differently-structured file --- #

def test_alt_profile_parses_thead_structure():
    set_profile_override(ALT_PROFILE)
    parsed = parse_html(ALT_SAMPLE)
    assert parsed.product_a == "Charlie One"
    assert parsed.product_b == "Delta Plus"
    assert len(parsed.features) == 6


def test_alt_profile_normalizes_icons_to_yes_no():
    set_profile_override(ALT_PROFILE)
    parsed = parse_html(ALT_SAMPLE)
    waterproof = next(f for f in parsed.features if f.name == "Waterproof")
    assert waterproof.value_a_raw == "yes"
    assert waterproof.value_b_raw == "no"


def test_alt_profile_extracts_metadata():
    set_profile_override(ALT_PROFILE)
    parsed = parse_html(ALT_SAMPLE)
    assert parsed.metadata.get("category") == "Smartphones"
