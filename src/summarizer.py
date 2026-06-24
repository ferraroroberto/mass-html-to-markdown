"""Second-pass feature-value abbreviation (issue #20).

The Markdown is generated from the ``features`` table, so this pass shortens the
*data*, not the rendered document. For every feature value whose word count
exceeds a limit, it produces a concise rewrite via an LLM and stores it in
``features.value_*_abbreviated``. Identical text is summarized exactly once
(deduplicated + cached in ``text_summaries``), which keeps the pass cheap and the
rendered output deterministic across re-runs.

Backends (selected by name, one ``summarize()`` interface):
- ``gemini``    — production, google-genai SDK (``GOOGLE_API_KEY``).
- ``local-hub`` — dev/CI, Anthropic SDK shape against the local LLM hub.
- ``fake``      — offline deterministic truncation; no network, used by tests
                  and for trying the whole flow without a key or the hub.

Pattern lifted from the sister project ``pdf-to-markdown`` (vertexai_backend /
hub_gemini_backend).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import load_config
from .database import (
    apply_abbreviation,
    fill_default_abbreviations,
    get_cached_summary,
    init_db,
    put_cached_summary,
    unique_long_values,
)
from .logging_utils import get_logger

logger = get_logger(__name__)

# Bump when the prompt text below changes meaning — it is part of the cache key,
# so a new version re-summarizes rather than serving a stale rewrite.
PROMPT_VERSION = "v1"

BACKENDS = ("gemini", "local-hub", "fake")

_DEFAULTS = {
    "word_limit": 40,
    "backend": "local-hub",
    "gemini_model": "gemini-2.5-pro",
    "hub_model": "claude-haiku-4-5",
    "hub_base_url": "http://127.0.0.1:8000",
}

_MAX_OUTPUT_TOKENS = 1024
_MAX_ATTEMPTS = 3
_BASE_DELAY_S = 2.0

_WS = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# Config + prompt
# --------------------------------------------------------------------------- #

def summarization_config() -> dict:
    """Merge the ``summarization`` block from config.json over built-in defaults."""
    cfg = load_config().get("summarization", {})
    return {**_DEFAULTS, **cfg}


def default_model(backend: str) -> str:
    cfg = summarization_config()
    return cfg["gemini_model"] if backend == "gemini" else cfg["hub_model"]


def build_prompt(word_limit: int) -> str:
    """The instruction sent with each snippet. Keep PROMPT_VERSION in sync."""
    return (
        "You are condensing one cell of a product-comparison table for a "
        "retrieval index. Rewrite the text below so it is much more concise, "
        f"using at most {word_limit} words. Preserve every concrete fact, number, "
        "unit, and product detail; drop only marketing fluff and repetition. "
        "Return a single line of plain text — no markdown, no bullet points, no "
        "quotes, no preamble, just the shortened text."
    )


def sanitize(text: str) -> str:
    """Collapse whitespace/newlines to a single line so an abbreviated value can
    never break a Markdown table row or split a bullet."""
    return _WS.sub(" ", text).strip()


def word_count(text: Optional[str]) -> int:
    return len(text.split()) if text else 0


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #

def summarize(
    text: str,
    *,
    word_limit: int,
    prompt: str,
    backend: str,
    model: str,
    base_url: Optional[str] = None,
) -> str:
    """Shorten one text via the chosen backend. Returns a sanitized single line."""
    if backend == "fake":
        return _summarize_fake(text, word_limit)
    if backend == "gemini":
        out = _retry(lambda: _summarize_gemini(text, prompt, model))
    elif backend == "local-hub":
        url = base_url or summarization_config()["hub_base_url"]
        out = _retry(lambda: _summarize_hub(text, prompt, model, url))
    else:
        raise ValueError(f"Unknown backend {backend!r}; expected one of {BACKENDS}")
    return sanitize(out)


def _retry(call: Callable[[], str]) -> str:
    """Exponential-backoff retry (2s, 4s, 8s) — mirrors pdf-to-markdown."""
    last: Optional[Exception] = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 — retry any transient failure
            last = exc
            if attempt == _MAX_ATTEMPTS:
                break
            delay = _BASE_DELAY_S * (2 ** (attempt - 1))
            logger.warning("LLM call failed (attempt %d/%d): %s — retrying in %.0fs",
                           attempt, _MAX_ATTEMPTS, exc, delay)
            time.sleep(delay)
    raise RuntimeError(f"LLM call failed after {_MAX_ATTEMPTS} attempts: {last}") from last


def _summarize_fake(text: str, word_limit: int) -> str:
    """Deterministic offline stand-in: keep the first *word_limit* words."""
    words = sanitize(text).split()
    if len(words) <= word_limit:
        return " ".join(words)
    return " ".join(words[:word_limit])


def _summarize_gemini(text: str, prompt: str, model: str) -> str:
    from google import genai  # lazy: optional dependency
    from google.genai import types

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not set; cannot use the gemini backend")
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model,
        contents=[f"{prompt}\n\n---\nText to shorten:\n{text}"],
        config=types.GenerateContentConfig(temperature=0.0),
    )
    return resp.text or ""


def _summarize_hub(text: str, prompt: str, model: str, base_url: str) -> str:
    from anthropic import Anthropic  # lazy: optional dependency

    client = Anthropic(api_key="local-dummy", base_url=base_url)
    resp = client.messages.create(
        model=model,
        max_tokens=_MAX_OUTPUT_TOKENS,
        temperature=0.0,
        messages=[
            {
                "role": "user",
                "content": f"{prompt}\n\n---\nText to shorten:\n{text}",
            }
        ],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

@dataclass
class AbbreviationStats:
    word_limit: int
    backend: str
    model: str
    dry_run: bool
    unique_long: int = 0      # distinct texts over the limit
    llm_calls: int = 0        # cache misses actually summarized (or estimated)
    cache_hits: int = 0       # reused from text_summaries
    cells_updated: int = 0    # over-limit cells written
    defaults_filled: int = 0  # under-limit cells copied raw -> abbreviated
    errors: list[str] = field(default_factory=list)


def run_abbreviation_pass(
    word_limit: int,
    backend: str,
    model: Optional[str] = None,
    prompt: Optional[str] = None,
    dry_run: bool = False,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> AbbreviationStats:
    """Summarize every over-limit feature value (deduplicated + cached).

    ``dry_run`` reports how many unique over-limit texts exist and how many would
    need a real LLM call (cache misses), without calling anything or writing.
    """
    init_db()
    model = model or default_model(backend)
    prompt = prompt or build_prompt(word_limit)

    uniques = unique_long_values(word_limit)
    stats = AbbreviationStats(
        word_limit=word_limit,
        backend=backend,
        model=model,
        dry_run=dry_run,
        unique_long=len(uniques),
    )

    if dry_run:
        stats.llm_calls = sum(
            1
            for t in uniques
            if get_cached_summary(t, word_limit, PROMPT_VERSION, model) is None
        )
        stats.cache_hits = stats.unique_long - stats.llm_calls
        logger.info(
            "Dry run: %d unique over-limit texts, %d would call the LLM, %d cached",
            stats.unique_long, stats.llm_calls, stats.cache_hits,
        )
        return stats

    total = len(uniques)
    for idx, text in enumerate(uniques, start=1):
        if progress:
            progress(idx, total, text[:60])
        cached = get_cached_summary(text, word_limit, PROMPT_VERSION, model)
        if cached is not None:
            short = cached
            stats.cache_hits += 1
        else:
            try:
                short = summarize(
                    text,
                    word_limit=word_limit,
                    prompt=prompt,
                    backend=backend,
                    model=model,
                )
            except Exception as exc:  # noqa: BLE001 — one bad text must not abort the batch
                logger.exception("Failed to summarize a value")
                stats.errors.append(str(exc))
                continue
            put_cached_summary(text, short, word_limit, PROMPT_VERSION, model)
            stats.llm_calls += 1
        stats.cells_updated += apply_abbreviation(text, short)

    # Under-limit cells: abbreviated == raw, so the column is fully populated.
    stats.defaults_filled = fill_default_abbreviations()
    logger.info(
        "Abbreviation pass done — unique=%d calls=%d cache_hits=%d cells=%d defaults=%d errors=%d",
        stats.unique_long, stats.llm_calls, stats.cache_hits,
        stats.cells_updated, stats.defaults_filled, len(stats.errors),
    )
    return stats
