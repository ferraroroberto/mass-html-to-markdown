"""End-to-end tests for the second-pass abbreviation feature (issue #20).

Hermetic: uses the offline ``fake`` backend (deterministic truncation) and an
isolated temp database — no network, no API key, no local hub required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import database
from src.database import connect, reconstruct_parsed, unique_long_values, upsert_comparison
from src.markdown_gen import render_markdown
from src.parser import parse_html, set_profile_override
from src.summarizer import run_abbreviation_pass, word_count
from src.validator import assert_same_skeleton, skeleton_problems

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = PROJECT_ROOT / "data" / "profiles" / "default.json"
# Verbose, neutral demo inputs that double as fixtures — same pattern as
# test_pipeline.py reading its primary sample from data/html.
SAMPLES = [
    PROJECT_ROOT / "data" / "html" / "NimbusOne_vs_OrbitX.html",
    PROJECT_ROOT / "data" / "html" / "NimbusOne_vs_PulsarGo.html",
]
WORD_LIMIT = 40


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    """Isolated DB seeded with the two verbose mock comparisons."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database, "db_path", lambda: db_file)
    set_profile_override(DEFAULT_PROFILE)
    database.init_db()
    for sample in SAMPLES:
        upsert_comparison(parse_html(sample), None)
    yield db_file
    set_profile_override(None)


def _total_long_cells() -> int:
    with connect() as conn:
        rows = conn.execute(
            "SELECT value_a_raw, value_b_raw FROM features"
        ).fetchall()
    n = 0
    for r in rows:
        n += word_count(r["value_a_raw"]) > WORD_LIMIT
        n += word_count(r["value_b_raw"]) > WORD_LIMIT
    return n


def test_dedup_summarizes_unique_text_once(seeded_db):
    total_long = _total_long_cells()
    unique = len(unique_long_values(WORD_LIMIT))

    # The shared support blurb appears in 3 cells but is one unique text, so the
    # unique count must be strictly below the raw occurrence count.
    assert unique < total_long

    stats = run_abbreviation_pass(WORD_LIMIT, backend="fake")
    assert stats.unique_long == unique
    assert stats.llm_calls == unique          # first run: every unique is a miss
    assert stats.cache_hits == 0
    assert stats.cells_updated == total_long   # every long cell got rewritten


def test_rerun_is_fully_cached(seeded_db):
    run_abbreviation_pass(WORD_LIMIT, backend="fake")
    again = run_abbreviation_pass(WORD_LIMIT, backend="fake")
    assert again.llm_calls == 0
    assert again.cache_hits == again.unique_long


def test_edited_prompt_bypasses_cache(seeded_db):
    """Regression for #24: the cache key folds in a hash of the actual prompt, so
    editing the prompt re-summarizes instead of silently serving the default
    prompt's cached rewrite. (The fake backend ignores the prompt text — what is
    under test here is the cache identity, not the output.)"""
    first = run_abbreviation_pass(WORD_LIMIT, backend="fake")
    assert first.llm_calls == first.unique_long

    edited = run_abbreviation_pass(
        WORD_LIMIT, backend="fake", prompt="A deliberately different instruction."
    )
    # Different prompt -> different cache key -> every unique misses again.
    assert edited.llm_calls == edited.unique_long
    assert edited.cache_hits == 0

    # Same edited prompt re-runs fully cached, proving the new key is stable.
    again = run_abbreviation_pass(
        WORD_LIMIT, backend="fake", prompt="A deliberately different instruction."
    )
    assert again.llm_calls == 0
    assert again.cache_hits == again.unique_long


def test_dry_run_makes_no_changes(seeded_db):
    dry = run_abbreviation_pass(WORD_LIMIT, backend="fake", dry_run=True)
    assert dry.llm_calls == dry.unique_long   # nothing cached yet -> all would call
    # Nothing was written: a real run still reports every unique as a fresh call.
    real = run_abbreviation_pass(WORD_LIMIT, backend="fake")
    assert real.cache_hits == 0


def test_abbreviated_values_within_limit(seeded_db):
    run_abbreviation_pass(WORD_LIMIT, backend="fake")
    with connect() as conn:
        rows = conn.execute(
            "SELECT value_a_abbreviated, value_b_abbreviated FROM features"
        ).fetchall()
    assert rows
    for r in rows:
        assert word_count(r["value_a_abbreviated"]) <= WORD_LIMIT
        assert word_count(r["value_b_abbreviated"]) <= WORD_LIMIT


def test_under_limit_values_untouched(seeded_db):
    run_abbreviation_pass(WORD_LIMIT, backend="fake")
    with connect() as conn:
        row = conn.execute(
            "SELECT value_a_raw, value_a_abbreviated FROM features "
            "WHERE feature_name = 'Starting price' LIMIT 1"
        ).fetchone()
    assert row["value_a_raw"] == row["value_a_abbreviated"]


def test_short_variant_preserves_skeleton(seeded_db):
    run_abbreviation_pass(WORD_LIMIT, backend="fake")
    with connect() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM comparisons").fetchall()]
    assert ids
    for cid in ids:
        parsed = reconstruct_parsed(cid)
        full = render_markdown(parsed, "full")
        short = render_markdown(parsed, "short")
        assert short != full  # the prose actually changed
        assert skeleton_problems(full, short) == []
        assert_same_skeleton(full, short)  # does not raise


def test_validator_catches_structural_drift():
    full = "## At a glance\n| Attr | A | B |\n|---|---|---|\n| Price | $1 | $2 |\n"
    # A short variant that dropped a table row entirely.
    short = "## At a glance\n| Attr | A | B |\n|---|---|---|\n"
    assert skeleton_problems(full, short)
    with pytest.raises(ValueError):
        assert_same_skeleton(full, short)
