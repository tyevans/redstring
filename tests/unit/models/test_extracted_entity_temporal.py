"""
Unit tests for ExtractedEntity temporal columns and properties.

Tests the timeline/chronology extraction features added in ADR-025:
- Temporal columns (start_date, end_date, date_precision, etc.)
- Temporal helper properties (is_temporal, date_precision_enum, etc.)
- DatePrecision and UncertaintyMarker enums
"""

import uuid
from datetime import UTC, datetime

import pytest

from kg_builder.models.extracted_entity import (
    DatePrecision,
    EntityType,
    ExtractedEntity,
    ExtractionMethod,
    UncertaintyMarker,
)


class TestDatePrecisionEnum:
    """Tests for DatePrecision enum."""

    def test_all_precision_values_exist(self):
        """Test all expected precision values exist."""
        assert DatePrecision.YEAR.value == "year"
        assert DatePrecision.MONTH.value == "month"
        assert DatePrecision.DAY.value == "day"
        assert DatePrecision.HOUR.value == "hour"
        assert DatePrecision.MINUTE.value == "minute"

    def test_precision_enum_from_string(self):
        """Test creating enum from string value."""
        assert DatePrecision("year") == DatePrecision.YEAR
        assert DatePrecision("day") == DatePrecision.DAY

    def test_precision_enum_invalid_value(self):
        """Test invalid precision value raises ValueError."""
        with pytest.raises(ValueError):
            DatePrecision("invalid")

    def test_precision_enum_is_string_subclass(self):
        """Test DatePrecision is a string enum."""
        assert isinstance(DatePrecision.YEAR, str)
        assert DatePrecision.YEAR == "year"


class TestUncertaintyMarkerEnum:
    """Tests for UncertaintyMarker enum."""

    def test_all_uncertainty_values_exist(self):
        """Test all expected uncertainty values exist."""
        assert UncertaintyMarker.EXACT.value == "exact"
        assert UncertaintyMarker.APPROXIMATE.value == "approximate"
        assert UncertaintyMarker.CIRCA.value == "circa"
        assert UncertaintyMarker.BEFORE.value == "before"
        assert UncertaintyMarker.AFTER.value == "after"
        assert UncertaintyMarker.INFERRED.value == "inferred"

    def test_uncertainty_enum_from_string(self):
        """Test creating enum from string value."""
        assert UncertaintyMarker("exact") == UncertaintyMarker.EXACT
        assert UncertaintyMarker("circa") == UncertaintyMarker.CIRCA

    def test_uncertainty_enum_invalid_value(self):
        """Test invalid uncertainty value raises ValueError."""
        with pytest.raises(ValueError):
            UncertaintyMarker("unknown")

    def test_uncertainty_enum_is_string_subclass(self):
        """Test UncertaintyMarker is a string enum."""
        assert isinstance(UncertaintyMarker.EXACT, str)
        assert UncertaintyMarker.EXACT == "exact"


class TestExtractedEntityTemporalColumns:
    """Tests for ExtractedEntity temporal column handling."""

    @pytest.fixture
    def base_entity_kwargs(self) -> dict:
        """Provide base kwargs for creating an ExtractedEntity."""
        return {
            "tenant_id": uuid.uuid4(),
            "source_page_id": uuid.uuid4(),
            "entity_type": EntityType.EVENT.value,
            "name": "Test Event",
            "normalized_name": "test event",
            "extraction_method": ExtractionMethod.LLM_OPENAI,
        }

    def test_temporal_columns_default_to_none(self, base_entity_kwargs):
        """Test all temporal columns default to None."""
        entity = ExtractedEntity(**base_entity_kwargs)

        assert entity.start_date is None
        assert entity.end_date is None
        assert entity.date_precision is None
        assert entity.uncertainty_marker is None
        assert entity.original_temporal_text is None
        assert entity.sequence_position is None
        assert entity.publication_date is None

    def test_start_date_can_be_set(self, base_entity_kwargs):
        """Test start_date can be set to a datetime."""
        now = datetime.now(UTC)
        entity = ExtractedEntity(**base_entity_kwargs, start_date=now)

        assert entity.start_date == now

    def test_end_date_can_be_set(self, base_entity_kwargs):
        """Test end_date can be set for date ranges."""
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2020, 12, 31, tzinfo=UTC)

        entity = ExtractedEntity(
            **base_entity_kwargs,
            start_date=start,
            end_date=end,
        )

        assert entity.start_date == start
        assert entity.end_date == end

    def test_date_precision_as_string(self, base_entity_kwargs):
        """Test date_precision can be set as a string."""
        entity = ExtractedEntity(
            **base_entity_kwargs,
            start_date=datetime(1999, 1, 1, tzinfo=UTC),
            date_precision="year",
        )

        assert entity.date_precision == "year"

    def test_uncertainty_marker_as_string(self, base_entity_kwargs):
        """Test uncertainty_marker can be set as a string."""
        entity = ExtractedEntity(
            **base_entity_kwargs,
            start_date=datetime(1500, 1, 1, tzinfo=UTC),
            uncertainty_marker="circa",
        )

        assert entity.uncertainty_marker == "circa"

    def test_original_temporal_text_preserved(self, base_entity_kwargs):
        """Test original_temporal_text stores the source text."""
        original_text = "in the late 1990s"
        entity = ExtractedEntity(
            **base_entity_kwargs,
            start_date=datetime(1997, 1, 1, tzinfo=UTC),
            date_precision="year",
            uncertainty_marker="approximate",
            original_temporal_text=original_text,
        )

        assert entity.original_temporal_text == original_text

    def test_sequence_position_for_undated_events(self, base_entity_kwargs):
        """Test sequence_position can be set without dates."""
        entity = ExtractedEntity(
            **base_entity_kwargs,
            sequence_position=3,
        )

        assert entity.sequence_position == 3
        assert entity.start_date is None

    def test_publication_date_separate_from_event_date(self, base_entity_kwargs):
        """Test publication_date is separate from event start_date."""
        event_date = datetime(1776, 7, 4, tzinfo=UTC)
        pub_date = datetime(2024, 1, 15, tzinfo=UTC)

        entity = ExtractedEntity(
            **base_entity_kwargs,
            start_date=event_date,
            publication_date=pub_date,
        )

        assert entity.start_date == event_date
        assert entity.publication_date == pub_date


class TestExtractedEntityTemporalProperties:
    """Tests for ExtractedEntity temporal helper properties."""

    @pytest.fixture
    def base_entity_kwargs(self) -> dict:
        """Provide base kwargs for creating an ExtractedEntity."""
        return {
            "tenant_id": uuid.uuid4(),
            "source_page_id": uuid.uuid4(),
            "entity_type": EntityType.EVENT.value,
            "name": "Test Event",
            "normalized_name": "test event",
            "extraction_method": ExtractionMethod.LLM_OPENAI,
        }

    # is_temporal property tests

    def test_is_temporal_false_when_no_temporal_data(self, base_entity_kwargs):
        """Test is_temporal is False with no temporal data."""
        entity = ExtractedEntity(**base_entity_kwargs)
        assert entity.is_temporal is False

    def test_is_temporal_true_with_start_date(self, base_entity_kwargs):
        """Test is_temporal is True when start_date is set."""
        entity = ExtractedEntity(
            **base_entity_kwargs,
            start_date=datetime.now(UTC),
        )
        assert entity.is_temporal is True

    def test_is_temporal_true_with_sequence_position(self, base_entity_kwargs):
        """Test is_temporal is True when only sequence_position is set."""
        entity = ExtractedEntity(
            **base_entity_kwargs,
            sequence_position=5,
        )
        assert entity.is_temporal is True

    def test_is_temporal_true_with_both(self, base_entity_kwargs):
        """Test is_temporal is True when both date and sequence are set."""
        entity = ExtractedEntity(
            **base_entity_kwargs,
            start_date=datetime.now(UTC),
            sequence_position=1,
        )
        assert entity.is_temporal is True

    # date_precision_enum property tests

    def test_date_precision_enum_none_when_not_set(self, base_entity_kwargs):
        """Test date_precision_enum returns None when not set."""
        entity = ExtractedEntity(**base_entity_kwargs)
        assert entity.date_precision_enum is None

    def test_date_precision_enum_returns_enum(self, base_entity_kwargs):
        """Test date_precision_enum returns correct enum value."""
        entity = ExtractedEntity(
            **base_entity_kwargs,
            date_precision="day",
        )
        assert entity.date_precision_enum == DatePrecision.DAY

    def test_date_precision_enum_none_for_invalid_value(self, base_entity_kwargs):
        """Test date_precision_enum returns None for invalid value."""
        entity = ExtractedEntity(**base_entity_kwargs)
        entity.date_precision = "invalid_precision"

        assert entity.date_precision_enum is None

    # uncertainty_marker_enum property tests

    def test_uncertainty_marker_enum_none_when_not_set(self, base_entity_kwargs):
        """Test uncertainty_marker_enum returns None when not set."""
        entity = ExtractedEntity(**base_entity_kwargs)
        assert entity.uncertainty_marker_enum is None

    def test_uncertainty_marker_enum_returns_enum(self, base_entity_kwargs):
        """Test uncertainty_marker_enum returns correct enum value."""
        entity = ExtractedEntity(
            **base_entity_kwargs,
            uncertainty_marker="approximate",
        )
        assert entity.uncertainty_marker_enum == UncertaintyMarker.APPROXIMATE

    def test_uncertainty_marker_enum_none_for_invalid_value(self, base_entity_kwargs):
        """Test uncertainty_marker_enum returns None for invalid value."""
        entity = ExtractedEntity(**base_entity_kwargs)
        entity.uncertainty_marker = "invalid_marker"

        assert entity.uncertainty_marker_enum is None

    # has_date_range property tests

    def test_has_date_range_false_with_no_dates(self, base_entity_kwargs):
        """Test has_date_range is False with no dates."""
        entity = ExtractedEntity(**base_entity_kwargs)
        assert entity.has_date_range is False

    def test_has_date_range_false_with_only_start(self, base_entity_kwargs):
        """Test has_date_range is False with only start_date."""
        entity = ExtractedEntity(
            **base_entity_kwargs,
            start_date=datetime.now(UTC),
        )
        assert entity.has_date_range is False

    def test_has_date_range_true_with_both_dates(self, base_entity_kwargs):
        """Test has_date_range is True with both dates."""
        entity = ExtractedEntity(
            **base_entity_kwargs,
            start_date=datetime(2020, 1, 1, tzinfo=UTC),
            end_date=datetime(2020, 12, 31, tzinfo=UTC),
        )
        assert entity.has_date_range is True


class TestExtractedEntityTemporalValidEnumValues:
    """Tests to verify all enum values work correctly with the model."""

    @pytest.fixture
    def base_entity_kwargs(self) -> dict:
        """Provide base kwargs for creating an ExtractedEntity."""
        return {
            "tenant_id": uuid.uuid4(),
            "source_page_id": uuid.uuid4(),
            "entity_type": EntityType.EVENT.value,
            "name": "Test Event",
            "normalized_name": "test event",
            "extraction_method": ExtractionMethod.LLM_OPENAI,
            "start_date": datetime.now(UTC),
        }

    @pytest.mark.parametrize("precision", [
        "year", "month", "day", "hour", "minute"
    ])
    def test_all_date_precision_values_valid(self, base_entity_kwargs, precision):
        """Test all DatePrecision values work with the model."""
        entity = ExtractedEntity(
            **base_entity_kwargs,
            date_precision=precision,
        )
        assert entity.date_precision == precision
        assert entity.date_precision_enum == DatePrecision(precision)

    @pytest.mark.parametrize("marker", [
        "exact", "approximate", "circa", "before", "after", "inferred"
    ])
    def test_all_uncertainty_marker_values_valid(self, base_entity_kwargs, marker):
        """Test all UncertaintyMarker values work with the model."""
        entity = ExtractedEntity(
            **base_entity_kwargs,
            uncertainty_marker=marker,
        )
        assert entity.uncertainty_marker == marker
        assert entity.uncertainty_marker_enum == UncertaintyMarker(marker)
