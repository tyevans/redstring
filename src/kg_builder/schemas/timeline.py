"""
Pydantic schemas for Timeline and Chronology Extraction feature.

This module provides Pydantic models for temporal data extraction,
timeline queries, and related API operations.

See ADR-025 for design decisions.
"""

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# Temporal relationship types as a Literal type for strict validation
TemporalRelationshipType = Literal[
    "precedes",
    "follows",
    "during",
    "overlaps",
    "causes",
    "concurrent",
]


class DatePrecision(str, Enum):
    """Precision level of temporal data.

    Used to indicate the granularity of extracted dates.
    For example, "1999" would have YEAR precision while
    "March 15, 1999 at 3:45 PM" would have MINUTE precision.
    """

    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    HOUR = "hour"
    MINUTE = "minute"


class UncertaintyMarker(str, Enum):
    """Uncertainty indicator for temporal data.

    Captures how certain we are about the extracted date:
    - EXACT: The date is explicitly stated ("on March 15, 1999")
    - APPROXIMATE: The date is approximate ("around 1999", "in the late 90s")
    - CIRCA: Historical approximation ("circa 1500")
    - BEFORE: Event occurred before a date ("before 1999")
    - AFTER: Event occurred after a date ("after 1999")
    - INFERRED: Date was inferred from context, not explicit
    """

    EXACT = "exact"
    APPROXIMATE = "approximate"
    CIRCA = "circa"
    BEFORE = "before"
    AFTER = "after"
    INFERRED = "inferred"


# =============================================================================
# Temporal Data Schemas
# =============================================================================


class TemporalData(BaseModel):
    """Temporal data associated with an entity.

    Contains all temporal metadata extracted from text, including
    date range, precision, uncertainty, and original source text.
    """

    start_date: datetime | None = Field(
        None,
        description="Event start date/time (ISO 8601, UTC normalized)",
    )
    end_date: datetime | None = Field(
        None,
        description="Event end date/time for ranges (ISO 8601, UTC normalized)",
    )
    precision: DatePrecision | None = Field(
        None,
        description="Precision level: year, month, day, hour, minute",
    )
    uncertainty: UncertaintyMarker | None = Field(
        None,
        description="Uncertainty indicator: exact, approximate, circa, before, after, inferred",
    )
    original_text: str | None = Field(
        None,
        description="Original temporal expression from source text",
    )
    sequence_position: int | None = Field(
        None,
        ge=0,
        description="Ordinal position for narrative/sequence ordering",
    )
    publication_date: datetime | None = Field(
        None,
        description="When the content was published (vs. when event occurred)",
    )

    @field_validator("precision", mode="before")
    @classmethod
    def validate_precision(cls, v):
        """Accept both string and enum values for precision."""
        if v is None:
            return None
        if isinstance(v, DatePrecision):
            return v
        if isinstance(v, str):
            try:
                return DatePrecision(v.lower())
            except ValueError as err:
                raise ValueError(
                    f"Invalid precision: {v}. Must be one of: {', '.join(e.value for e in DatePrecision)}"
                ) from err
        raise ValueError(f"Invalid precision type: {type(v)}")

    @field_validator("uncertainty", mode="before")
    @classmethod
    def validate_uncertainty(cls, v):
        """Accept both string and enum values for uncertainty."""
        if v is None:
            return None
        if isinstance(v, UncertaintyMarker):
            return v
        if isinstance(v, str):
            try:
                return UncertaintyMarker(v.lower())
            except ValueError as err:
                raise ValueError(
                    f"Invalid uncertainty: {v}. Must be one of: {', '.join(e.value for e in UncertaintyMarker)}"
                ) from err
        raise ValueError(f"Invalid uncertainty type: {type(v)}")


class TemporalDataCreate(BaseModel):
    """Input schema for creating/updating temporal data on an entity.

    Used when adding temporal information to an existing entity
    or during extraction.
    """

    start_date: datetime | None = Field(
        None,
        description="Event start date/time (ISO 8601)",
    )
    end_date: datetime | None = Field(
        None,
        description="Event end date/time for ranges (ISO 8601)",
    )
    precision: DatePrecision | None = Field(
        None,
        description="Precision level: year, month, day, hour, minute",
    )
    uncertainty: UncertaintyMarker | None = Field(
        None,
        description="Uncertainty indicator: exact, approximate, circa, before, after, inferred",
    )
    original_text: str | None = Field(
        None,
        max_length=1000,
        description="Original temporal expression from source text",
    )
    sequence_position: int | None = Field(
        None,
        ge=0,
        description="Ordinal position for narrative/sequence ordering",
    )
    publication_date: datetime | None = Field(
        None,
        description="When the content was published",
    )

    @field_validator("precision", mode="before")
    @classmethod
    def validate_precision(cls, v):
        """Accept both string and enum values for precision."""
        if v is None:
            return None
        if isinstance(v, DatePrecision):
            return v
        if isinstance(v, str):
            try:
                return DatePrecision(v.lower())
            except ValueError as err:
                raise ValueError(
                    f"Invalid precision: {v}. Must be one of: {', '.join(e.value for e in DatePrecision)}"
                ) from err
        raise ValueError(f"Invalid precision type: {type(v)}")

    @field_validator("uncertainty", mode="before")
    @classmethod
    def validate_uncertainty(cls, v):
        """Accept both string and enum values for uncertainty."""
        if v is None:
            return None
        if isinstance(v, UncertaintyMarker):
            return v
        if isinstance(v, str):
            try:
                return UncertaintyMarker(v.lower())
            except ValueError as err:
                raise ValueError(
                    f"Invalid uncertainty: {v}. Must be one of: {', '.join(e.value for e in UncertaintyMarker)}"
                ) from err
        raise ValueError(f"Invalid uncertainty type: {type(v)}")


# =============================================================================
# Timeline Query Schemas
# =============================================================================


class TimeRange(BaseModel):
    """A time range with start and end dates."""

    start: datetime = Field(..., description="Range start date/time (ISO 8601)")
    end: datetime = Field(..., description="Range end date/time (ISO 8601)")


class EntityReference(BaseModel):
    """Reference to an entity, used in timeline events."""

    id: UUID = Field(..., description="Entity ID")
    name: str = Field(..., description="Entity name")
    entity_type: str = Field(..., description="Entity type")


class TimelineEvent(BaseModel):
    """A single event on the timeline.

    Represents an entity with temporal data, formatted for timeline display.
    """

    id: UUID = Field(..., description="Entity ID")
    name: str = Field(..., description="Event/entity name")
    description: str | None = Field(None, description="Event description")
    entity_type: str = Field(..., description="Entity type")
    start_date: datetime | None = Field(
        None,
        description="Event start date/time (ISO 8601)",
    )
    end_date: datetime | None = Field(
        None,
        description="Event end date/time (ISO 8601)",
    )
    precision: DatePrecision | None = Field(
        None,
        description="Date precision level",
    )
    uncertainty: UncertaintyMarker | None = Field(
        None,
        description="Uncertainty indicator",
    )
    original_text: str | None = Field(
        None,
        description="Original temporal expression",
    )
    sequence_position: int | None = Field(
        None,
        description="Sequence position for undated events",
    )
    source_page_id: UUID = Field(..., description="Source page ID")
    source_url: str = Field(..., description="Source page URL")
    involved_entities: list[EntityReference] = Field(
        default_factory=list,
        description="Related entities",
    )
    # Source attribution fields (populated for project-level timeline queries)
    source_job_id: UUID | None = Field(
        None,
        description="ID of the job that extracted this event (for project timelines)",
    )
    source_job_name: str | None = Field(
        None,
        description="Name of the source job (for project timelines)",
    )

    class Config:
        from_attributes = True


class TimelineFilters(BaseModel):
    """Filters for timeline queries."""

    start_date: datetime | None = Field(
        None,
        description="Filter events starting after this date",
    )
    end_date: datetime | None = Field(
        None,
        description="Filter events ending before this date",
    )
    entity_types: list[str] | None = Field(
        None,
        description="Filter by entity types",
    )
    search: str | None = Field(
        None,
        max_length=200,
        description="Search term for event names/descriptions",
    )
    include_undated: bool = Field(
        True,
        description="Include events with only sequence_position (no dates)",
    )
    sort_by: str = Field(
        "date",
        description="Sort by: date or sequence",
    )

    @field_validator("sort_by")
    @classmethod
    def validate_sort_by(cls, v):
        """Validate sort_by value."""
        allowed = ["date", "sequence"]
        if v not in allowed:
            raise ValueError(f"sort_by must be one of: {', '.join(allowed)}")
        return v


class TemporalRelationship(BaseModel):
    """A temporal relationship between two timeline events.

    Represents temporal ordering, causation, or overlap between events.
    These relationships capture how events relate to each other in time.

    Relationship Types:
    - precedes: Event A happened before Event B
    - follows: Event A happened after Event B
    - during: Event A occurred while Event B was ongoing
    - overlaps: Events A and B share some time period
    - causes: Event A caused Event B (implies A precedes B)
    - concurrent: Events A and B happened at the same time
    """

    id: UUID = Field(
        ...,
        description="Unique identifier for this relationship",
    )
    source_event_id: UUID = Field(
        ...,
        description="ID of the source event (the 'A' in 'A precedes B')",
    )
    target_event_id: UUID = Field(
        ...,
        description="ID of the target event (the 'B' in 'A precedes B')",
    )
    source_event_name: str = Field(
        ...,
        description="Name of the source event for display",
    )
    target_event_name: str = Field(
        ...,
        description="Name of the target event for display",
    )
    relationship_type: TemporalRelationshipType = Field(
        ...,
        description="Type of temporal relationship",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score for this relationship (0.0-1.0)",
    )
    evidence: str | None = Field(
        None,
        description="Source text or context supporting this relationship",
    )
    is_inferred: bool = Field(
        False,
        description="Whether this relationship was inferred from dates rather than extracted",
    )

    class Config:
        from_attributes = True


class TimelineResponse(BaseModel):
    """Response for timeline queries."""

    events: list[TimelineEvent] = Field(
        ...,
        description="Timeline events",
    )
    relationships: list[TemporalRelationship] = Field(
        default_factory=list,
        description="Temporal relationships between events",
    )
    total_count: int = Field(
        ...,
        description="Total number of events matching the query",
    )
    time_range: TimeRange | None = Field(
        None,
        description="Overall time range of returned events",
    )
    has_more: bool = Field(
        ...,
        description="Whether more events are available",
    )
    undated_count: int = Field(
        ...,
        description="Number of events without dates",
    )
    sequence_only_count: int = Field(
        ...,
        description="Events with sequence but no date",
    )
    relationship_count: int = Field(
        0,
        description="Total number of temporal relationships",
    )


class TimelineSummaryResponse(BaseModel):
    """Summary statistics for a timeline."""

    total_events: int = Field(
        ...,
        description="Total temporal events",
    )
    dated_events: int = Field(
        ...,
        description="Events with explicit dates",
    )
    undated_events: int = Field(
        ...,
        description="Events without dates",
    )
    sequence_only_events: int = Field(
        ...,
        description="Events with only sequence position",
    )
    time_range: TimeRange | None = Field(
        None,
        description="Overall time range",
    )
    entity_type_counts: dict[str, int] = Field(
        ...,
        description="Count by entity type",
    )
    precision_distribution: dict[str, int] = Field(
        ...,
        description="Count by date precision",
    )
    uncertainty_distribution: dict[str, int] = Field(
        ...,
        description="Count by uncertainty marker",
    )
