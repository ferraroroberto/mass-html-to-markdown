"""Shared data contracts used by the parser, database, and Markdown generator."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FeatureRow(BaseModel):
    """A single feature/attribute row from a comparison page."""

    name: str
    category: Optional[str] = None
    value_a_raw: Optional[str] = None
    value_a_numeric: Optional[float] = None
    value_b_raw: Optional[str] = None
    value_b_numeric: Optional[float] = None
    winner: Optional[str] = None  # "A", "B", "tie", or None
    # Second-pass shortened values (issue #20). None until the abbreviation pass
    # runs; renderers fall back to the raw value when these are unset, so a
    # freshly-ingested comparison still renders identically to before.
    value_a_abbreviated: Optional[str] = None
    value_b_abbreviated: Optional[str] = None


class ParsedComparison(BaseModel):
    """The result of parsing one HTML comparator page."""

    filename: str
    product_a: str
    product_b: str
    metadata: dict = Field(default_factory=dict)
    features: list[FeatureRow] = Field(default_factory=list)
    source_hash: str
    parsed_at: datetime = Field(default_factory=_utcnow)
