"""
Unit tests for the Temporal Relationship Inference Service.

Tests the inference of temporal relationships between events based on
their date information.
"""

import uuid
from datetime import UTC, datetime

import pytest

from kg_builder.services.temporal_relationship_inference import (
    InferredTemporalRelationship,
    TemporalEvent,
    TemporalRelationshipInferenceService,
    get_temporal_relationship_inference_service,
)


class TestTemporalEvent:
    """Tests for TemporalEvent model."""

    def test_temporal_event_creation(self):
        """Test TemporalEvent with all fields."""
        event_id = uuid.uuid4()
        event = TemporalEvent(
            id=event_id,
            name="Test Event",
            start_date=datetime(2020, 1, 1, tzinfo=UTC),
            end_date=datetime(2020, 12, 31, tzinfo=UTC),
        )

        assert event.id == event_id
        assert event.name == "Test Event"
        assert event.start_date is not None
        assert event.end_date is not None

    def test_temporal_event_no_end_date(self):
        """Test TemporalEvent with only start date."""
        event = TemporalEvent(
            id=uuid.uuid4(),
            name="Point Event",
            start_date=datetime(2020, 6, 15, tzinfo=UTC),
        )

        assert event.start_date is not None
        assert event.end_date is None


class TestTemporalRelationshipInferenceService:
    """Tests for TemporalRelationshipInferenceService."""

    @pytest.fixture
    def service(self):
        """Create inference service instance."""
        return TemporalRelationshipInferenceService()

    def test_infer_precedes(self, service):
        """Test inference of PRECEDES relationship."""
        event_a = TemporalEvent(
            id=uuid.uuid4(),
            name="WWI",
            start_date=datetime(1914, 7, 28, tzinfo=UTC),
            end_date=datetime(1918, 11, 11, tzinfo=UTC),
        )
        event_b = TemporalEvent(
            id=uuid.uuid4(),
            name="WWII",
            start_date=datetime(1939, 9, 1, tzinfo=UTC),
            end_date=datetime(1945, 9, 2, tzinfo=UTC),
        )

        relationships = service.infer_relationships([event_a, event_b])

        assert len(relationships) == 1
        rel = relationships[0]
        assert rel.relationship_type == "precedes"
        assert rel.source_event_id == event_a.id
        assert rel.target_event_id == event_b.id
        assert rel.is_inferred is True
        assert "Inferred from dates" in rel.evidence

    def test_infer_follows(self, service):
        """Test inference of FOLLOWS relationship.

        When the first event in the list comes AFTER the second event,
        the service infers a 'follows' relationship.
        """
        event_a = TemporalEvent(
            id=uuid.uuid4(),
            name="WWII",
            start_date=datetime(1939, 9, 1, tzinfo=UTC),
            end_date=datetime(1945, 9, 2, tzinfo=UTC),
        )
        event_b = TemporalEvent(
            id=uuid.uuid4(),
            name="WWI",
            start_date=datetime(1914, 7, 28, tzinfo=UTC),
            end_date=datetime(1918, 11, 11, tzinfo=UTC),
        )

        relationships = service.infer_relationships([event_a, event_b])

        assert len(relationships) == 1
        rel = relationships[0]
        # Event A starts after Event B ends, so A follows B
        assert rel.relationship_type == "follows"
        assert rel.source_event_id == event_a.id
        assert rel.target_event_id == event_b.id

    def test_infer_concurrent(self, service):
        """Test inference of CONCURRENT relationship."""
        event_a = TemporalEvent(
            id=uuid.uuid4(),
            name="Event A",
            start_date=datetime(2020, 1, 1, tzinfo=UTC),
            end_date=datetime(2020, 12, 31, tzinfo=UTC),
        )
        event_b = TemporalEvent(
            id=uuid.uuid4(),
            name="Event B",
            start_date=datetime(2020, 1, 1, tzinfo=UTC),
            end_date=datetime(2020, 12, 31, tzinfo=UTC),
        )

        relationships = service.infer_relationships([event_a, event_b])

        assert len(relationships) == 1
        rel = relationships[0]
        assert rel.relationship_type == "concurrent"
        assert rel.is_inferred is True

    def test_infer_during(self, service):
        """Test inference of DURING relationship."""
        # Battle of Stalingrad (contained within WWII)
        outer_event = TemporalEvent(
            id=uuid.uuid4(),
            name="WWII",
            start_date=datetime(1939, 9, 1, tzinfo=UTC),
            end_date=datetime(1945, 9, 2, tzinfo=UTC),
        )
        inner_event = TemporalEvent(
            id=uuid.uuid4(),
            name="Battle of Stalingrad",
            start_date=datetime(1942, 8, 23, tzinfo=UTC),
            end_date=datetime(1943, 2, 2, tzinfo=UTC),
        )

        relationships = service.infer_relationships([outer_event, inner_event])

        assert len(relationships) == 1
        rel = relationships[0]
        assert rel.relationship_type == "during"
        assert rel.source_event_id == inner_event.id
        assert rel.target_event_id == outer_event.id

    def test_infer_overlaps(self, service):
        """Test inference of OVERLAPS relationship."""
        event_a = TemporalEvent(
            id=uuid.uuid4(),
            name="Renaissance",
            start_date=datetime(1300, 1, 1, tzinfo=UTC),
            end_date=datetime(1600, 12, 31, tzinfo=UTC),
        )
        event_b = TemporalEvent(
            id=uuid.uuid4(),
            name="Age of Exploration",
            start_date=datetime(1450, 1, 1, tzinfo=UTC),
            end_date=datetime(1700, 12, 31, tzinfo=UTC),
        )

        relationships = service.infer_relationships([event_a, event_b])

        assert len(relationships) == 1
        rel = relationships[0]
        assert rel.relationship_type == "overlaps"

    def test_no_inference_without_temporal_data(self, service):
        """Test no relationships inferred for events without dates."""
        event_a = TemporalEvent(
            id=uuid.uuid4(),
            name="Event A",
            start_date=None,
        )
        event_b = TemporalEvent(
            id=uuid.uuid4(),
            name="Event B",
            start_date=None,
        )

        relationships = service.infer_relationships([event_a, event_b])

        assert len(relationships) == 0

    def test_single_event_no_inference(self, service):
        """Test no relationships inferred with only one event."""
        event = TemporalEvent(
            id=uuid.uuid4(),
            name="Single Event",
            start_date=datetime(2020, 1, 1, tzinfo=UTC),
        )

        relationships = service.infer_relationships([event])

        assert len(relationships) == 0

    def test_empty_events_list(self, service):
        """Test no relationships inferred for empty list."""
        relationships = service.infer_relationships([])

        assert len(relationships) == 0

    def test_skip_existing_relationships(self, service):
        """Test existing relationship pairs are skipped."""
        event_a = TemporalEvent(
            id=uuid.uuid4(),
            name="Event A",
            start_date=datetime(2020, 1, 1, tzinfo=UTC),
            end_date=datetime(2020, 6, 30, tzinfo=UTC),
        )
        event_b = TemporalEvent(
            id=uuid.uuid4(),
            name="Event B",
            start_date=datetime(2020, 7, 1, tzinfo=UTC),
            end_date=datetime(2020, 12, 31, tzinfo=UTC),
        )

        # Mark this pair as having existing relationship
        existing_pairs = {(event_a.id, event_b.id)}

        relationships = service.infer_relationships(
            [event_a, event_b],
            existing_relationship_pairs=existing_pairs,
        )

        assert len(relationships) == 0

    def test_confidence_with_full_range(self, service):
        """Test confidence is high with full date ranges."""
        event_a = TemporalEvent(
            id=uuid.uuid4(),
            name="Event A",
            start_date=datetime(2020, 1, 1, tzinfo=UTC),
            end_date=datetime(2020, 6, 30, tzinfo=UTC),
        )
        event_b = TemporalEvent(
            id=uuid.uuid4(),
            name="Event B",
            start_date=datetime(2020, 7, 1, tzinfo=UTC),
            end_date=datetime(2020, 12, 31, tzinfo=UTC),
        )

        relationships = service.infer_relationships([event_a, event_b])

        assert len(relationships) == 1
        assert relationships[0].confidence == service.CONFIDENCE_FULL_RANGE

    def test_confidence_with_single_dates(self, service):
        """Test confidence is lower with single dates (no end dates)."""
        event_a = TemporalEvent(
            id=uuid.uuid4(),
            name="Event A",
            start_date=datetime(2020, 1, 1, tzinfo=UTC),
        )
        event_b = TemporalEvent(
            id=uuid.uuid4(),
            name="Event B",
            start_date=datetime(2020, 7, 1, tzinfo=UTC),
        )

        relationships = service.infer_relationships([event_a, event_b])

        assert len(relationships) == 1
        assert relationships[0].confidence == service.CONFIDENCE_SINGLE_DATE

    def test_multiple_events(self, service):
        """Test inference with multiple events."""
        events = [
            TemporalEvent(
                id=uuid.uuid4(),
                name="Event 1",
                start_date=datetime(2020, 1, 1, tzinfo=UTC),
                end_date=datetime(2020, 3, 31, tzinfo=UTC),
            ),
            TemporalEvent(
                id=uuid.uuid4(),
                name="Event 2",
                start_date=datetime(2020, 4, 1, tzinfo=UTC),
                end_date=datetime(2020, 6, 30, tzinfo=UTC),
            ),
            TemporalEvent(
                id=uuid.uuid4(),
                name="Event 3",
                start_date=datetime(2020, 7, 1, tzinfo=UTC),
                end_date=datetime(2020, 9, 30, tzinfo=UTC),
            ),
        ]

        relationships = service.infer_relationships(events)

        # Should have 3 relationships: 1->2, 1->3, 2->3
        assert len(relationships) == 3

        # All should be precedes relationships
        for rel in relationships:
            assert rel.relationship_type == "precedes"


class TestInferredTemporalRelationship:
    """Tests for InferredTemporalRelationship model."""

    def test_inferred_relationship_creation(self):
        """Test InferredTemporalRelationship creation."""
        rel = InferredTemporalRelationship(
            id=uuid.uuid4(),
            source_event_id=uuid.uuid4(),
            target_event_id=uuid.uuid4(),
            source_event_name="Event A",
            target_event_name="Event B",
            relationship_type="precedes",
            confidence=0.85,
            evidence="Inferred from dates",
            is_inferred=True,
        )

        assert rel.relationship_type == "precedes"
        assert rel.confidence == 0.85
        assert rel.is_inferred is True


class TestGetTemporalRelationshipInferenceService:
    """Tests for factory function."""

    def test_get_service(self):
        """Test factory function returns service instance."""
        service = get_temporal_relationship_inference_service()

        assert isinstance(service, TemporalRelationshipInferenceService)
