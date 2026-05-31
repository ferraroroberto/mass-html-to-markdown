"""Centralized logging configuration."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from .config import load_config, resolve_path


_ROOT_LOGGER_NAME = "comparator"
_configured = False


def _configure_root() -> None:
    """Configure the single root logger ('comparator') exactly once."""
    global _configured
    if _configured:
        return

    cfg = load_config()
    log_dir = resolve_path(cfg["paths"]["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = RotatingFileHandler(
        log_dir / "comparator.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    root = logging.getLogger(_ROOT_LOGGER_NAME)
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    root.propagate = False

    _configured = True


def get_logger(name: str = _ROOT_LOGGER_NAME) -> logging.Logger:
    """Return a logger for *name*.

    The first call configures the 'comparator' parent logger with file and
    stream handlers.  All subsequent calls — regardless of *name* — return
    a child logger (e.g. 'comparator.src.parser') that propagates to the
    already-configured parent, so every module's records reach both handlers.
    """
    _configure_root()

    # Ensure child loggers sit under the configured root so propagation works.
    if name != _ROOT_LOGGER_NAME and not name.startswith(_ROOT_LOGGER_NAME + "."):
        name = f"{_ROOT_LOGGER_NAME}.{name}"

    return logging.getLogger(name)
