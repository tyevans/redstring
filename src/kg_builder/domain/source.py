"""The `SourceDocument` domain type: what a caller hands the library.

The library never fetches content itself — a `SourceDocument` is always
supplied by the caller.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

from kg_builder.domain.ids import SourceId


class SourceDocument(BaseModel):
    """A piece of content, supplied by the caller, to build a graph from."""

    id: SourceId
    text: str
    uri: str | None = None
    title: str | None = None
    published_at: datetime | None = None
    metadata: dict[str, Any] = {}

    @field_validator("text")
    @classmethod
    def _require_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value

    @field_validator("published_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        return value
