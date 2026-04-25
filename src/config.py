"""Config loader. Reads config.json from project root."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Load config.json, falling back to config.json.example on first run."""
    cfg_path = PROJECT_ROOT / "config.json"
    if not cfg_path.exists():
        example = PROJECT_ROOT / "config.json.example"
        if example.exists():
            cfg_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            raise FileNotFoundError("config.json and config.json.example both missing")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def resolve_path(relative_or_absolute: str) -> Path:
    """Resolve a path from config relative to the project root."""
    p = Path(relative_or_absolute)
    return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


def save_config(cfg: dict[str, Any]) -> None:
    """Persist config.json. Used by the Config tab in Streamlit."""
    cfg_path = PROJECT_ROOT / "config.json"
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    load_config.cache_clear()
