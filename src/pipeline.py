"""End-to-end ingestion pipeline.

CLI:
    python -m src.pipeline ingest [--input PATH] [--force] [--limit N]
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv

# Populate os.environ from .env before any local import runs — importing
# .database / .logging_utils below configures logging at module load time and
# reads LOG_LEVEL, so .env must be loaded first for the documented var to apply.
load_dotenv()

from .config import load_config, resolve_path  # noqa: E402
from .database import (  # noqa: E402
    existing_hash,
    init_db,
    list_comparisons,
    reconstruct_parsed,
    upsert_comparison,
)
from .logging_utils import get_logger  # noqa: E402
from .markdown_gen import render_markdown, write_markdown  # noqa: E402
from .parser import parse_html, set_profile_override  # noqa: E402
from .summarizer import run_abbreviation_pass  # noqa: E402
from .validator import assert_same_skeleton  # noqa: E402


logger = get_logger(__name__)


@dataclass
class IngestResult:
    filename: str
    status: str  # "parsed" | "skipped" | "error"
    message: str = ""
    comparison_id: Optional[int] = None


def ingest_folder(
    input_dir: Path,
    force: bool = False,
    limit: Optional[int] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
    profile: Optional[Path] = None,
) -> list[IngestResult]:
    """Ingest every HTML file in ``input_dir``. Returns per-file results.

    ``profile`` overrides the profile from config.json for this run only.
    """
    if profile is not None:
        set_profile_override(profile)
    init_db()
    files = sorted(input_dir.glob("*.html"))
    if limit:
        files = files[:limit]

    results: list[IngestResult] = []
    total = len(files)
    logger.info("Ingesting %d HTML files from %s", total, input_dir)

    t0 = time.time()
    for idx, html_path in enumerate(files, start=1):
        if progress:
            progress(idx, total, html_path.name)
        try:
            parsed = parse_html(html_path)
            prior = existing_hash(parsed.filename)
            if prior == parsed.source_hash and not force:
                results.append(
                    IngestResult(html_path.name, "skipped", "unchanged")
                )
                continue

            md_path = write_markdown(parsed)
            cid = upsert_comparison(parsed, str(md_path.relative_to(resolve_path("."))))
            results.append(
                IngestResult(html_path.name, "parsed", f"{len(parsed.features)} features", cid)
            )
        except Exception as exc:  # noqa: BLE001 — batch must keep going
            logger.exception("Failed to ingest %s", html_path.name)
            results.append(IngestResult(html_path.name, "error", str(exc)))

    elapsed = time.time() - t0
    ok = sum(1 for r in results if r.status == "parsed")
    skipped = sum(1 for r in results if r.status == "skipped")
    errors = sum(1 for r in results if r.status == "error")
    logger.info(
        "Done in %.1fs — parsed=%d skipped=%d errors=%d", elapsed, ok, skipped, errors
    )
    return results


def render_variant(variant: str = "short", validate: bool = True) -> list[Path]:
    """Re-render every stored comparison to *variant* Markdown from the database.

    For ``short`` with ``validate``, each file's skeleton is checked against the
    full render so structural drift surfaces immediately.
    """
    init_db()
    paths: list[Path] = []
    for c in list_comparisons():
        parsed = reconstruct_parsed(c["id"])
        if parsed is None:
            continue
        out_path = write_markdown(parsed, variant=variant)
        if validate and variant == "short":
            assert_same_skeleton(render_markdown(parsed, "full"), render_markdown(parsed, "short"))
        paths.append(out_path)
    logger.info("Rendered %d %s Markdown files", len(paths), variant)
    return paths


def _cli() -> int:
    cfg = load_config()
    parser = argparse.ArgumentParser(prog="pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="Ingest HTML files")
    ing.add_argument("--input", default=cfg["paths"]["html_input_dir"])
    ing.add_argument(
        "--profile",
        default=None,
        help="Path to a profile JSON; overrides config.json for this run.",
    )
    ing.add_argument("--force", action="store_true")
    ing.add_argument("--limit", type=int, default=None)

    sm_cfg = cfg.get("summarization", {})
    summ = sub.add_parser("summarize", help="Second pass: LLM-abbreviate long feature values")
    summ.add_argument("--word-limit", type=int, default=sm_cfg.get("word_limit", 40))
    summ.add_argument(
        "--backend", default=sm_cfg.get("backend", "local-hub"),
        choices=["gemini", "local-hub", "fake"],
    )
    summ.add_argument("--model", default=None, help="Override the backend's default model.")
    summ.add_argument("--dry-run", action="store_true", help="Report counts only; no LLM calls.")

    rnd = sub.add_parser("render", help="Re-render Markdown for a variant from the database")
    rnd.add_argument("--variant", default="short", choices=["full", "short"])
    rnd.add_argument("--no-validate", action="store_true", help="Skip the skeleton check.")

    args = parser.parse_args()
    if args.cmd == "ingest":
        profile_path = resolve_path(args.profile) if args.profile else None
        results = ingest_folder(
            resolve_path(args.input),
            force=args.force,
            limit=args.limit,
            profile=profile_path,
        )
        for r in results:
            logger.info("[%7s] %s  %s", r.status, r.filename, r.message)
        errors = sum(1 for r in results if r.status == "error")
        return 1 if errors else 0

    if args.cmd == "summarize":
        stats = run_abbreviation_pass(
            word_limit=args.word_limit,
            backend=args.backend,
            model=args.model,
            dry_run=args.dry_run,
        )
        logger.info(
            "%s — unique over-limit=%d, LLM calls=%d, cache hits=%d, cells updated=%d, errors=%d",
            "DRY RUN" if stats.dry_run else "DONE",
            stats.unique_long, stats.llm_calls, stats.cache_hits,
            stats.cells_updated, len(stats.errors),
        )
        return 1 if stats.errors else 0

    if args.cmd == "render":
        paths = render_variant(variant=args.variant, validate=not args.no_validate)
        logger.info("Wrote %d %s file(s)", len(paths), args.variant)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
