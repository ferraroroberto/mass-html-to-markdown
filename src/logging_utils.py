"""Centralized logging configuration."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import load_config, resolve_path


_configured = False


def get_logger(name: str = "comparator") -> logging.Logger:
    global _configured
    logger = logging.getLogger(name)
    if _configured:
        return logger

    cfg = load_config()
    log_dir = resolve_path(cfg["paths"]["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = RotatingFileHandler(
        log_dir / "comparator.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False

    _configured = True
    return logger
