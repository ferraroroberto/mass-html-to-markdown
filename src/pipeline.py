"""End-to-end ingestion pipeline.

CLI:
    python -m src.pipeline ingest [--input PATH] [--output PATH] [--force] [--limit N]
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import load_config, resolve_path
from .database import existing_hash, init_db, upsert_comparison
from .logging_utils import get_logger
from .markdown_gen import write_markdown
from .parser import parse_html, set_profile_override


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


def _cli() -> int:
    cfg = load_config()
    parser = argparse.ArgumentParser(prog="pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="Ingest HTML files")
    ing.add_argument("--input", default=cfg["paths"]["html_input_dir"])
    ing.add_argument("--output", default=cfg["paths"]["markdown_output_dir"])
    ing.add_argument(
        "--profile",
        default=None,
        help="Path to a profile JSON; overrides config.json for this run.",
    )
    ing.add_argument("--force", action="store_true")
    ing.add_argument("--limit", type=int, default=None)

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
            print(f"[{r.status:>7}] {r.filename}  {r.message}")
        errors = sum(1 for r in results if r.status == "error")
        return 1 if errors else 0
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
