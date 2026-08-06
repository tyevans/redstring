"""Temporal value objects for the domain model.

`DatePrecision` and `UncertaintyMarker` live here and nowhere else. They were
briefly duplicated against the ORM models this package replaced; slice 9
deleted those, so this module is the single definition. Both are public API.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, field_validator, model_validator


class DatePrecision(StrEnum):
    """Precision level of temporal data."""

    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    HOUR = "hour"
    MINUTE = "minute"


class UncertaintyMarker(StrEnum):
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
