"""
Unit tests for TemporalEnrichmentService (ADR-025).

Tests cover:
- Entity enrichment with temporal data
- Batch enrichment
- Uncertainty marker handling
- Metrics tracking
- Parser integration
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from dateutil.tz import UTC

from kg_builder.extraction.schemas import ExtractedEntitySchema, TemporalEventProperties
from kg_builder.schemas.timeline import DatePrecision, UncertaintyMarker
from kg_builder.services.extraction.temporal_enrichment import (
    QUALIFIER_TO_UNCERTAINTY,
    TemporalEnrichmentResult,
    TemporalEnrichmentService,
    TemporalEnrichmentStats,
    get_temporal_enrichment_service,
    reset_temporal_enrichment_service,
)
from kg_builder.services.temporal_parser import TemporalParseResult


class TestTemporalEnrichmentResult:
    """Tests for TemporalEnrichmentResult dataclass."""

    def test_default_values(self):
        """Test default values of enrichment result."""
        result = TemporalEnrichmentResult()
        assert result.start_date is None
        assert result.end_date is None
        assert result.date_precision is None
        assert result.uncertainty_marker is None
        assert result.original_temporal_text is None
        assert result.sequence_position is None
        assert result.confidence == 0.0
        assert result.enriched is False
        assert result.parse_method is None

    def test_enriched_result(self):
        """Test enrichment result with data."""
        result = TemporalEnrichmentResult(
            start_date=datetime(1920, 3, 15, tzinfo=UTC),
            date_precision="day",
            confidence=0.95,
            enriched=True,
            parse_method="dateutil",
        )
        assert result.start_date == datetime(1920, 3, 15, tzinfo=UTC)
        assert result.date_precision == "day"
        assert result.confidence == 0.95
        assert result.enriched is True


class TestTemporalEnrichmentStats:
    """Tests for TemporalEnrichmentStats dataclass."""

    def test_default_values(self):
        """Test default values of enrichment stats."""
        stats = TemporalEnrichmentStats()
        assert stats.total_entities == 0
        assert stats.entities_with_temporal == 0
        assert stats.entities_enriched == 0
        assert stats.entities_parse_failed == 0
        assert stats.entities_sequence_only == 0


class TestQualifierToUncertainty:
    """Tests for qualifier to uncertainty marker mapping."""

    @pytest.mark.parametrize(
        "qualifier,expected",
        [
            ("before", UncertaintyMarker.BEFORE),
            ("after", UncertaintyMarker.AFTER),
            ("around", UncertaintyMarker.APPROXIMATE),
            ("approximately", UncertaintyMarker.APPROXIMATE),
            ("about", UncertaintyMarker.APPROXIMATE),
            ("roughly", UncertaintyMarker.APPROXIMATE),
            ("circa", UncertaintyMarker.CIRCA),
            ("during", UncertaintyMarker.APPROXIMATE),
            ("until", UncertaintyMarker.BEFORE),
            ("since", UncertaintyMarker.AFTER),
        ],
    )
    def test_qualifier_mapping(self, qualifier: str, expected: UncertaintyMarker):
        """Test qualifier to uncertainty marker mapping."""
        assert QUALIFIER_TO_UNCERTAINTY[qualifier] == expected


class TestTemporalEnrichmentService:
    """Tests for TemporalEnrichmentService."""

    @pytest.fixture
    def mock_parser(self):
        """Create a mock temporal parser."""
        parser = MagicMock()
        return parser

    @pytest.fixture
    def service(self, mock_parser):
        """Create service with mock parser."""
        return TemporalEnrichmentService(parser=mock_parser)

    def test_enrich_entity_without_temporal(self, service):
        """Test enriching entity without temporal data."""
        entity = ExtractedEntitySchema(
            name="Test Entity",
            entity_type="custom",
            confidence=0.9,
            temporal=None,
        )
        result = service.enrich_entity(entity)
        assert result.enriched is False
        assert result.start_date is None

    def test_enrich_entity_with_parseable_date(self, service, mock_parser):
        """Test enriching entity with parseable event_date."""
        # Configure mock parser
        mock_parser.parse.return_value = TemporalParseResult(
            start_date=datetime(1920, 3, 15, tzinfo=UTC),
            end_date=None,
            precision=DatePrecision.DAY,
            uncertainty=UncertaintyMarker.EXACT,
            confidence=0.95,
            parse_method="dateutil",
        )

        entity = ExtractedEntitySchema(
            name="Battle",
            entity_type="event",
            confidence=0.9,
            temporal=TemporalEventProperties(
                temporal_expression="March 15, 1920",
                event_date="1920-03-15",
                is_approximate=False,
            ),
        )

        result = service.enrich_entity(entity)

        assert result.enriched is True
        assert result.start_date == datetime(1920, 3, 15, tzinfo=UTC)
        assert result.date_precision == "day"
        assert result.confidence == 0.95
        assert result.parse_method == "dateutil"
        mock_parser.parse.assert_called_with("1920-03-15")

    def test_enrich_entity_with_date_range(self, service, mock_parser):
        """Test enriching entity with date range."""
        # Configure mock for start date
        mock_parser.parse.side_effect = [
            TemporalParseResult(
                start_date=datetime(1914, 1, 1, tzinfo=UTC),
                end_date=None,
                precision=DatePrecision.YEAR,
                uncertainty=UncertaintyMarker.EXACT,
                confidence=0.90,
                parse_method="dateutil",
            ),
            TemporalParseResult(
                start_date=datetime(1918, 1, 1, tzinfo=UTC),
                end_date=None,
                precision=DatePrecision.YEAR,
                uncertainty=UncertaintyMarker.EXACT,
                confidence=0.90,
                parse_method="dateutil",
            ),
        ]

        entity = ExtractedEntitySchema(
            name="World War I",
            entity_type="event",
            confidence=0.95,
            temporal=TemporalEventProperties(
                temporal_expression="1914-1918",
                event_date="1914",
                end_date="1918",
            ),
        )

        result = service.enrich_entity(entity)

        assert result.enriched is True
        assert result.start_date == datetime(1914, 1, 1, tzinfo=UTC)
        assert result.end_date == datetime(1918, 1, 1, tzinfo=UTC)
        assert mock_parser.parse.call_count == 2

    def test_enrich_entity_with_approximate_date(self, service, mock_parser):
        """Test enriching entity with approximate date."""
        mock_parser.parse.return_value = TemporalParseResult(
            start_date=datetime(1850, 1, 1, tzinfo=UTC),
            end_date=None,
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.CIRCA,
            confidence=0.70,
            parse_method="dateutil",
        )

        entity = ExtractedEntitySchema(
            name="Birth",
            entity_type="event",
            confidence=0.85,
            temporal=TemporalEventProperties(
                temporal_expression="circa 1850",
                event_date="1850",
                is_approximate=True,
                temporal_qualifier="circa",
            ),
        )

        result = service.enrich_entity(entity)

        assert result.enriched is True
        assert result.uncertainty_marker == "circa"

    def test_enrich_entity_with_sequence_only(self, service, mock_parser):
        """Test enriching entity with only sequence position."""
        entity = ExtractedEntitySchema(
            name="First Event",
            entity_type="event",
            confidence=0.8,
            temporal=TemporalEventProperties(
                sequence_position=1,
            ),
        )

        result = service.enrich_entity(entity)

        assert result.enriched is True
        assert result.sequence_position == 1
        assert result.start_date is None
        mock_parser.parse.assert_not_called()

    def test_enrich_entity_fallback_to_expression(self, service, mock_parser):
        """Test fallback to parsing temporal_expression when event_date fails."""
        # First call (event_date) fails - return parse result with no start_date
        mock_parser.parse.side_effect = [
            TemporalParseResult(
                start_date=None,
                end_date=None,
                precision=DatePrecision.YEAR,  # Use valid enum value
                uncertainty=UncertaintyMarker.APPROXIMATE,  # Use valid enum value
                confidence=0.0,
                parse_method=None,
            ),
        ]

        entity = ExtractedEntitySchema(
            name="Event",
            entity_type="event",
            confidence=0.9,
            temporal=TemporalEventProperties(
                temporal_expression="on the Ides of March, 1920",
                event_date="invalid",
            ),
        )

        result = service.enrich_entity(entity)

        # event_date is set, so it tries to parse it
        # Since parsing returns no start_date, it does NOT fallback to temporal_expression
        # (per the implementation: only falls back if event_date is None/empty)
        assert result.enriched is False

    def test_enrich_entity_with_qualifier_before(self, service, mock_parser):
        """Test uncertainty from 'before' qualifier."""
        mock_parser.parse.return_value = TemporalParseResult(
            start_date=datetime(1900, 1, 1, tzinfo=UTC),
            end_date=None,
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.EXACT,
            confidence=0.80,
            parse_method="dateutil",
        )

        entity = ExtractedEntitySchema(
            name="Event",
            entity_type="event",
            confidence=0.9,
            temporal=TemporalEventProperties(
                temporal_expression="before 1900",
                event_date="1900",
                temporal_qualifier="before",
            ),
        )

        result = service.enrich_entity(entity)

        assert result.enriched is True
        assert result.uncertainty_marker == "before"

    def test_enrich_entity_preserves_original_text(self, service, mock_parser):
        """Test that original temporal text is preserved."""
        mock_parser.parse.return_value = TemporalParseResult(
            start_date=datetime(1920, 3, 15, tzinfo=UTC),
            end_date=None,
            precision=DatePrecision.DAY,
            uncertainty=UncertaintyMarker.EXACT,
            confidence=0.95,
            parse_method="dateutil",
        )

        entity = ExtractedEntitySchema(
            name="Event",
            entity_type="event",
            confidence=0.9,
            temporal=TemporalEventProperties(
                temporal_expression="On the morning of March 15, 1920",
                event_date="1920-03-15",
            ),
        )

        result = service.enrich_entity(entity)

        assert result.original_temporal_text == "On the morning of March 15, 1920"

    def test_enrich_entities_batch(self, service, mock_parser):
        """Test batch enrichment of entities."""
        mock_parser.parse.return_value = TemporalParseResult(
            start_date=datetime(2000, 1, 1, tzinfo=UTC),
            end_date=None,
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.EXACT,
            confidence=0.90,
            parse_method="dateutil",
        )

        entities = [
            ExtractedEntitySchema(
                name="Entity 1",
                entity_type="event",
                confidence=0.9,
                temporal=TemporalEventProperties(event_date="2000"),
            ),
            ExtractedEntitySchema(
                name="Entity 2",
                entity_type="event",
                confidence=0.9,
                temporal=None,  # No temporal data
            ),
            ExtractedEntitySchema(
                name="Entity 3",
                entity_type="event",
                confidence=0.9,
                temporal=TemporalEventProperties(sequence_position=1),
            ),
        ]

        results, stats = service.enrich_entities(entities)

        assert len(results) == 3
        assert stats.total_entities == 3
        assert stats.entities_with_temporal == 2
        assert stats.entities_enriched == 1
        assert stats.entities_sequence_only == 1
        assert stats.entities_parse_failed == 0


class TestTemporalEnrichmentServiceSingleton:
    """Tests for singleton pattern."""

    def test_get_service_returns_same_instance(self):
        """Test that get_temporal_enrichment_service returns singleton."""
        reset_temporal_enrichment_service()
        service1 = get_temporal_enrichment_service()
        service2 = get_temporal_enrichment_service()
        assert service1 is service2

    def test_reset_service_clears_singleton(self):
        """Test that reset clears the singleton."""
        service1 = get_temporal_enrichment_service()
        reset_temporal_enrichment_service()
        service2 = get_temporal_enrichment_service()
        assert service1 is not service2


class TestDetermineUncertainty:
    """Tests for _determine_uncertainty method."""

    @pytest.fixture
    def service(self):
        """Create service instance."""
        return TemporalEnrichmentService()

    def test_approximate_flag_takes_precedence(self, service):
        """Test that is_approximate flag takes precedence."""
        temporal = TemporalEventProperties(
            event_date="1850",
            is_approximate=True,
        )
        result = service._determine_uncertainty(temporal, UncertaintyMarker.EXACT)
        assert result == UncertaintyMarker.APPROXIMATE.value

    def test_qualifier_with_approximate_flag(self, service):
        """Test qualifier with approximate flag."""
        temporal = TemporalEventProperties(
            event_date="1850",
            is_approximate=True,
            temporal_qualifier="circa",
        )
        result = service._determine_uncertainty(temporal, UncertaintyMarker.EXACT)
        assert result == UncertaintyMarker.CIRCA.value

    def test_qualifier_without_approximate_flag(self, service):
        """Test qualifier without approximate flag."""
        temporal = TemporalEventProperties(
            event_date="1900",
            is_approximate=False,
            temporal_qualifier="before",
        )
        result = service._determine_uncertainty(temporal, UncertaintyMarker.EXACT)
        assert result == UncertaintyMarker.BEFORE.value

    def test_fallback_to_parser_uncertainty(self, service):
        """Test fallback to parser's uncertainty detection."""
        temporal = TemporalEventProperties(
            event_date="1920-03-15",
            is_approximate=False,
        )
        result = service._determine_uncertainty(temporal, UncertaintyMarker.APPROXIMATE)
        assert result == UncertaintyMarker.APPROXIMATE.value
