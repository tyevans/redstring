"""
Unit tests for timeline Pydantic schemas.

Tests the DatePrecision, UncertaintyMarker enums and related schemas
from the Timeline and Chronology Extraction feature (ADR-025).
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from kg_builder.schemas.timeline import (
    DatePrecision,
    EntityReference,
    TemporalData,
    TemporalDataCreate,
    TemporalRelationship,
    TimelineEvent,
    TimelineFilters,
    TimelineResponse,
    TimelineSummaryResponse,
    TimeRange,
    UncertaintyMarker,
)


class TestDatePrecisionEnum:
    """Tests for DatePrecision Pydantic enum."""

    def test_all_precision_values(self):
        """Test all DatePrecision values are accessible."""
        assert DatePrecision.YEAR == "year"
        assert DatePrecision.MONTH == "month"
        assert DatePrecision.DAY == "day"
        assert DatePrecision.HOUR == "hour"
        assert DatePrecision.MINUTE == "minute"

    def test_precision_enum_is_string(self):
        """Test DatePrecision values are strings."""
        assert isinstance(DatePrecision.YEAR, str)


class TestUncertaintyMarkerEnum:
    """Tests for UncertaintyMarker Pydantic enum."""

    def test_all_uncertainty_values(self):
        """Test all UncertaintyMarker values are accessible."""
        assert UncertaintyMarker.EXACT == "exact"
        assert UncertaintyMarker.APPROXIMATE == "approximate"
        assert UncertaintyMarker.CIRCA == "circa"
        assert UncertaintyMarker.BEFORE == "before"
        assert UncertaintyMarker.AFTER == "after"
        assert UncertaintyMarker.INFERRED == "inferred"

    def test_uncertainty_enum_is_string(self):
        """Test UncertaintyMarker values are strings."""
        assert isinstance(UncertaintyMarker.EXACT, str)


class TestTemporalData:
    """Tests for TemporalData schema."""

    def test_empty_temporal_data(self):
        """Test TemporalData with all None fields."""
        data = TemporalData()

        assert data.start_date is None
        assert data.end_date is None
        assert data.precision is None
        assert data.uncertainty is None
        assert data.original_text is None
        assert data.sequence_position is None
        assert data.publication_date is None

    def test_temporal_data_with_dates(self):
        """Test TemporalData with dates."""
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2020, 12, 31, tzinfo=UTC)

        data = TemporalData(
            start_date=start,
            end_date=end,
            precision=DatePrecision.DAY,
            uncertainty=UncertaintyMarker.EXACT,
        )

        assert data.start_date == start
        assert data.end_date == end
        assert data.precision == DatePrecision.DAY
        assert data.uncertainty == UncertaintyMarker.EXACT

    def test_temporal_data_precision_from_string(self):
        """Test precision accepts string values."""
        data = TemporalData(
            start_date=datetime.now(UTC),
            precision="year",
        )

        assert data.precision == DatePrecision.YEAR

    def test_temporal_data_uncertainty_from_string(self):
        """Test uncertainty accepts string values."""
        data = TemporalData(
            start_date=datetime.now(UTC),
            uncertainty="circa",
        )

        assert data.uncertainty == UncertaintyMarker.CIRCA

    def test_temporal_data_invalid_precision(self):
        """Test invalid precision raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            TemporalData(precision="invalid")

        assert "Invalid precision" in str(exc_info.value)

    def test_temporal_data_invalid_uncertainty(self):
        """Test invalid uncertainty raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            TemporalData(uncertainty="invalid")

        assert "Invalid uncertainty" in str(exc_info.value)

    def test_temporal_data_with_sequence(self):
        """Test TemporalData with sequence_position only."""
        data = TemporalData(sequence_position=3)

        assert data.sequence_position == 3
        assert data.start_date is None

    def test_temporal_data_sequence_positive_or_zero(self):
        """Test sequence_position must be non-negative."""
        data = TemporalData(sequence_position=0)
        assert data.sequence_position == 0

        with pytest.raises(ValidationError):
            TemporalData(sequence_position=-1)


class TestTemporalDataCreate:
    """Tests for TemporalDataCreate schema."""

    def test_temporal_data_create_validation(self):
        """Test TemporalDataCreate accepts valid data."""
        data = TemporalDataCreate(
            start_date=datetime.now(UTC),
            precision="day",
            uncertainty="exact",
            original_text="on January 15, 2024",
        )

        assert data.precision == DatePrecision.DAY
        assert data.uncertainty == UncertaintyMarker.EXACT
        assert data.original_text == "on January 15, 2024"

    def test_temporal_data_create_max_original_text(self):
        """Test original_text has max length constraint."""
        long_text = "a" * 1001  # Exceeds 1000 char limit

        with pytest.raises(ValidationError):
            TemporalDataCreate(original_text=long_text)

    def test_temporal_data_create_precision_case_insensitive(self):
        """Test precision validation is case insensitive."""
        data = TemporalDataCreate(precision="YEAR")
        assert data.precision == DatePrecision.YEAR

        data = TemporalDataCreate(precision="Year")
        assert data.precision == DatePrecision.YEAR


class TestTimeRange:
    """Tests for TimeRange schema."""

    def test_time_range_creation(self):
        """Test TimeRange with start and end dates."""
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2020, 12, 31, tzinfo=UTC)

        time_range = TimeRange(start=start, end=end)

        assert time_range.start == start
        assert time_range.end == end

    def test_time_range_requires_both_dates(self):
        """Test TimeRange requires both start and end."""
        with pytest.raises(ValidationError):
            TimeRange(start=datetime.now(UTC))

        with pytest.raises(ValidationError):
            TimeRange(end=datetime.now(UTC))


class TestEntityReference:
    """Tests for EntityReference schema."""

    def test_entity_reference_creation(self):
        """Test EntityReference with valid data."""
        entity_id = uuid4()

        ref = EntityReference(
            id=entity_id,
            name="John Doe",
            entity_type="person",
        )

        assert ref.id == entity_id
        assert ref.name == "John Doe"
        assert ref.entity_type == "person"

    def test_entity_reference_requires_all_fields(self):
        """Test EntityReference requires all fields."""
        with pytest.raises(ValidationError):
            EntityReference(id=uuid4(), name="Test")


class TestTimelineEvent:
    """Tests for TimelineEvent schema."""

    def test_timeline_event_minimal(self):
        """Test TimelineEvent with minimal required fields."""
        event = TimelineEvent(
            id=uuid4(),
            name="Test Event",
            entity_type="event",
            source_page_id=uuid4(),
            source_url="https://example.com/page",
        )

        assert event.name == "Test Event"
        assert event.description is None
        assert event.start_date is None
        assert event.involved_entities == []

    def test_timeline_event_with_temporal_data(self):
        """Test TimelineEvent with full temporal data."""
        event = TimelineEvent(
            id=uuid4(),
            name="Historical Event",
            description="An important event",
            entity_type="event",
            start_date=datetime(1776, 7, 4, tzinfo=UTC),
            end_date=datetime(1776, 7, 4, 23, 59, 59, tzinfo=UTC),
            precision=DatePrecision.DAY,
            uncertainty=UncertaintyMarker.EXACT,
            original_text="July 4th, 1776",
            source_page_id=uuid4(),
            source_url="https://example.com/history",
        )

        assert event.precision == DatePrecision.DAY
        assert event.uncertainty == UncertaintyMarker.EXACT
        assert event.original_text == "July 4th, 1776"

    def test_timeline_event_with_involved_entities(self):
        """Test TimelineEvent with related entities."""
        entity_refs = [
            EntityReference(id=uuid4(), name="Person A", entity_type="person"),
            EntityReference(id=uuid4(), name="Location B", entity_type="location"),
        ]

        event = TimelineEvent(
            id=uuid4(),
            name="Meeting",
            entity_type="event",
            source_page_id=uuid4(),
            source_url="https://example.com",
            involved_entities=entity_refs,
        )

        assert len(event.involved_entities) == 2
        assert event.involved_entities[0].name == "Person A"


class TestTimelineFilters:
    """Tests for TimelineFilters schema."""

    def test_timeline_filters_defaults(self):
        """Test TimelineFilters default values."""
        filters = TimelineFilters()

        assert filters.start_date is None
        assert filters.end_date is None
        assert filters.entity_types is None
        assert filters.search is None
        assert filters.include_undated is True
        assert filters.sort_by == "date"

    def test_timeline_filters_with_values(self):
        """Test TimelineFilters with custom values."""
        filters = TimelineFilters(
            start_date=datetime(2020, 1, 1, tzinfo=UTC),
            end_date=datetime(2020, 12, 31, tzinfo=UTC),
            entity_types=["event", "person"],
            search="important",
            include_undated=False,
            sort_by="sequence",
        )

        assert filters.entity_types == ["event", "person"]
        assert filters.include_undated is False
        assert filters.sort_by == "sequence"

    def test_timeline_filters_invalid_sort_by(self):
        """Test TimelineFilters rejects invalid sort_by."""
        with pytest.raises(ValidationError) as exc_info:
            TimelineFilters(sort_by="invalid")

        assert "sort_by must be one of" in str(exc_info.value)

    def test_timeline_filters_search_max_length(self):
        """Test TimelineFilters search has max length."""
        long_search = "a" * 201

        with pytest.raises(ValidationError):
            TimelineFilters(search=long_search)


class TestTemporalRelationship:
    """Tests for TemporalRelationship schema."""

    def test_temporal_relationship_creation(self):
        """Test TemporalRelationship with valid data."""
        source_id = uuid4()
        target_id = uuid4()

        rel = TemporalRelationship(
            id=uuid4(),
            source_event_id=source_id,
            target_event_id=target_id,
            source_event_name="WWI",
            target_event_name="WWII",
            relationship_type="precedes",
            confidence=0.95,
            evidence="WWI preceded WWII",
        )

        assert rel.source_event_id == source_id
        assert rel.target_event_id == target_id
        assert rel.relationship_type == "precedes"
        assert rel.confidence == 0.95
        assert rel.is_inferred is False

    def test_temporal_relationship_all_types(self):
        """Test all valid temporal relationship types."""
        valid_types = ["precedes", "follows", "during", "overlaps", "causes", "concurrent"]

        for rel_type in valid_types:
            rel = TemporalRelationship(
                id=uuid4(),
                source_event_id=uuid4(),
                target_event_id=uuid4(),
                source_event_name="Event A",
                target_event_name="Event B",
                relationship_type=rel_type,
                confidence=0.8,
            )
            assert rel.relationship_type == rel_type

    def test_temporal_relationship_invalid_type(self):
        """Test invalid relationship type raises error."""
        with pytest.raises(ValidationError):
            TemporalRelationship(
                id=uuid4(),
                source_event_id=uuid4(),
                target_event_id=uuid4(),
                source_event_name="Event A",
                target_event_name="Event B",
                relationship_type="invalid_type",
                confidence=0.8,
            )

    def test_temporal_relationship_confidence_bounds(self):
        """Test confidence must be between 0 and 1."""
        base_data = {
            "id": uuid4(),
            "source_event_id": uuid4(),
            "target_event_id": uuid4(),
            "source_event_name": "Event A",
            "target_event_name": "Event B",
            "relationship_type": "precedes",
        }

        # Valid bounds
        TemporalRelationship(**base_data, confidence=0.0)
        TemporalRelationship(**base_data, confidence=1.0)
        TemporalRelationship(**base_data, confidence=0.5)

        # Invalid - below 0
        with pytest.raises(ValidationError):
            TemporalRelationship(**base_data, confidence=-0.1)

        # Invalid - above 1
        with pytest.raises(ValidationError):
            TemporalRelationship(**base_data, confidence=1.1)

    def test_temporal_relationship_inferred_flag(self):
        """Test is_inferred flag for inferred relationships."""
        rel = TemporalRelationship(
            id=uuid4(),
            source_event_id=uuid4(),
            target_event_id=uuid4(),
            source_event_name="Event A",
            target_event_name="Event B",
            relationship_type="precedes",
            confidence=0.75,
            evidence="Inferred from dates",
            is_inferred=True,
        )

        assert rel.is_inferred is True

    def test_temporal_relationship_optional_evidence(self):
        """Test evidence is optional."""
        rel = TemporalRelationship(
            id=uuid4(),
            source_event_id=uuid4(),
            target_event_id=uuid4(),
            source_event_name="Event A",
            target_event_name="Event B",
            relationship_type="causes",
            confidence=0.9,
        )

        assert rel.evidence is None


class TestTimelineResponse:
    """Tests for TimelineResponse schema."""

    def test_timeline_response_empty(self):
        """Test TimelineResponse with empty results."""
        response = TimelineResponse(
            events=[],
            total_count=0,
            time_range=None,
            has_more=False,
            undated_count=0,
            sequence_only_count=0,
        )

        assert len(response.events) == 0
        assert response.total_count == 0
        assert response.relationships == []
        assert response.relationship_count == 0

    def test_timeline_response_with_events(self):
        """Test TimelineResponse with events."""
        events = [
            TimelineEvent(
                id=uuid4(),
                name="Event 1",
                entity_type="event",
                source_page_id=uuid4(),
                source_url="https://example.com/1",
            ),
            TimelineEvent(
                id=uuid4(),
                name="Event 2",
                entity_type="event",
                source_page_id=uuid4(),
                source_url="https://example.com/2",
            ),
        ]

        response = TimelineResponse(
            events=events,
            total_count=10,
            time_range=TimeRange(
                start=datetime(2020, 1, 1, tzinfo=UTC),
                end=datetime(2020, 12, 31, tzinfo=UTC),
            ),
            has_more=True,
            undated_count=2,
            sequence_only_count=1,
        )

        assert len(response.events) == 2
        assert response.total_count == 10
        assert response.has_more is True

    def test_timeline_response_with_relationships(self):
        """Test TimelineResponse with temporal relationships."""
        event1_id = uuid4()
        event2_id = uuid4()

        events = [
            TimelineEvent(
                id=event1_id,
                name="WWI",
                entity_type="event",
                source_page_id=uuid4(),
                source_url="https://example.com/1",
            ),
            TimelineEvent(
                id=event2_id,
                name="WWII",
                entity_type="event",
                source_page_id=uuid4(),
                source_url="https://example.com/2",
            ),
        ]

        relationships = [
            TemporalRelationship(
                id=uuid4(),
                source_event_id=event1_id,
                target_event_id=event2_id,
                source_event_name="WWI",
                target_event_name="WWII",
                relationship_type="precedes",
                confidence=0.95,
                evidence="WWI preceded WWII",
            ),
        ]

        response = TimelineResponse(
            events=events,
            relationships=relationships,
            total_count=2,
            time_range=None,
            has_more=False,
            undated_count=0,
            sequence_only_count=0,
            relationship_count=1,
        )

        assert len(response.relationships) == 1
        assert response.relationship_count == 1
        assert response.relationships[0].relationship_type == "precedes"


class TestTimelineSummaryResponse:
    """Tests for TimelineSummaryResponse schema."""

    def test_timeline_summary_response(self):
        """Test TimelineSummaryResponse with statistics."""
        summary = TimelineSummaryResponse(
            total_events=100,
            dated_events=80,
            undated_events=20,
            sequence_only_events=15,
            time_range=TimeRange(
                start=datetime(1900, 1, 1, tzinfo=UTC),
                end=datetime(2024, 12, 31, tzinfo=UTC),
            ),
            entity_type_counts={"event": 60, "person": 30, "location": 10},
            precision_distribution={"year": 40, "month": 25, "day": 15},
            uncertainty_distribution={"exact": 50, "approximate": 20, "inferred": 10},
        )

        assert summary.total_events == 100
        assert summary.dated_events == 80
        assert summary.entity_type_counts["event"] == 60
        assert summary.precision_distribution["year"] == 40

    def test_timeline_summary_without_time_range(self):
        """Test TimelineSummaryResponse without time range."""
        summary = TimelineSummaryResponse(
            total_events=5,
            dated_events=0,
            undated_events=5,
            sequence_only_events=5,
            time_range=None,
            entity_type_counts={"event": 5},
            precision_distribution={},
            uncertainty_distribution={},
        )

        assert summary.time_range is None
        assert summary.dated_events == 0
