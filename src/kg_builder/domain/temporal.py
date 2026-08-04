"""Temporal value objects for the domain model.

`DatePrecision` and `UncertaintyMarker` are copied here from
`models/extracted_entity.py` (re-exported from `schemas/timeline.py`).
The duplication is intentional and temporary — see BACKLOG.md item B26,
which tracks moving the originals to this module in slice 9.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, field_validator, model_validator


class DatePrecision(str, Enum):
    """Precision level of temporal data."""

    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    HOUR = "hour"
    MINUTE = "minute"


class UncertaintyMarker(str, Enum):
    """Uncertainty indicator for temporal data."""

    EXACT = "exact"
    APPROXIMATE = "approximate"
    CIRCA = "circa"
    BEFORE = "before"
    AFTER = "after"
    INFERRED = "inferred"


class TemporalExtent(BaseModel):
    """When something happened, and how confidently we know it."""

    start_date: datetime | None = None
    end_date: datetime | None = None
    precision: DatePrecision | None = None
    uncertainty: UncertaintyMarker | None = None
    original_text: str | None = None
    sequence_position: int | None = None
    publication_date: datetime | None = None

    @field_validator("start_date", "end_date", "publication_date")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return value

    @field_validator("sequence_position")
    @classmethod
    def _require_non_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("sequence_position must be >= 0")
        return value

    @model_validator(mode="after")
    def _require_ordered_range(self) -> TemporalExtent:
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date must be >= start_date")
        return self

    @property
    def is_empty(self) -> bool:
        return all(
            value is None
            for value in (
                self.start_date,
                self.end_date,
                self.precision,
                self.uncertainty,
                self.original_text,
                self.sequence_position,
                self.publication_date,
            )
        )

    @property
    def has_range(self) -> bool:
        return self.start_date is not None and self.end_date is not None
