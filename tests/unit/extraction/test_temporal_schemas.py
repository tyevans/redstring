"""
Unit tests for temporal extraction schemas (ADR-025).

Tests cover:
- TemporalEventProperties validation
- ExtractedEntitySchema temporal field
- Temporal relationship type detection
- Schema field validation
"""

import pytest
from pydantic import ValidationError

from kg_builder.extraction.schemas import (
    ExtractedEntitySchema,
    TemporalEventProperties,
    is_temporal_relationship_type,
)


class TestTemporalEventProperties:
    """Tests for TemporalEventProperties schema."""

    def test_minimal_valid_properties(self):
        """Test creating properties with minimal data."""
        props = TemporalEventProperties()
        assert props.temporal_expression is None
        assert props.event_date is None
        assert props.end_date is None
        assert props.is_approximate is False
        assert props.temporal_qualifier is None
        assert props.sequence_position is None

    def test_full_valid_properties(self):
        """Test creating properties with all fields."""
        props = TemporalEventProperties(
            temporal_expression="March 15, 1920",
            event_date="1920-03-15",
            end_date="1920-12-31",
            is_approximate=False,
            temporal_qualifier="on",
            sequence_position=1,
        )
        assert props.temporal_expression == "March 15, 1920"
        assert props.event_date == "1920-03-15"
        assert props.end_date == "1920-12-31"
        assert props.is_approximate is False
        assert props.temporal_qualifier == "on"
        assert props.sequence_position == 1

    def test_approximate_date(self):
        """Test properties with approximate date."""
        props = TemporalEventProperties(
            temporal_expression="circa 1850",
            event_date="1850",
            is_approximate=True,
            temporal_qualifier="circa",
        )
        assert props.temporal_expression == "circa 1850"
        assert props.is_approximate is True
        assert props.temporal_qualifier == "circa"

    def test_date_range(self):
        """Test properties with date range."""
        props = TemporalEventProperties(
            temporal_expression="1914-1918",
            event_date="1914",
            end_date="1918",
            is_approximate=False,
        )
        assert props.event_date == "1914"
        assert props.end_date == "1918"

    def test_temporal_qualifier_normalization(self):
        """Test that temporal qualifier is normalized to lowercase."""
        props = TemporalEventProperties(
            temporal_qualifier="BEFORE",
        )
        assert props.temporal_qualifier == "before"

        props2 = TemporalEventProperties(
            temporal_qualifier="  After  ",
        )
        assert props2.temporal_qualifier == "after"

    def test_sequence_position_validation(self):
        """Test sequence position validation."""
        # Valid sequence position
        props = TemporalEventProperties(sequence_position=0)
        assert props.sequence_position == 0

        props = TemporalEventProperties(sequence_position=100)
        assert props.sequence_position == 100

        # Invalid: negative sequence position
        with pytest.raises(ValidationError) as exc_info:
            TemporalEventProperties(sequence_position=-1)
        assert "greater than or equal to 0" in str(exc_info.value)

    def test_temporal_expression_max_length(self):
        """Test temporal expression max length validation."""
        # Valid length
        props = TemporalEventProperties(temporal_expression="a" * 500)
        assert len(props.temporal_expression) == 500

        # Invalid: too long
        with pytest.raises(ValidationError) as exc_info:
            TemporalEventProperties(temporal_expression="a" * 501)
        assert "String should have at most 500 characters" in str(exc_info.value)

    def test_event_date_max_length(self):
        """Test event_date max length validation."""
        # Valid ISO date
        props = TemporalEventProperties(event_date="2024-03-15T10:30:00Z")
        assert props.event_date == "2024-03-15T10:30:00Z"

        # Invalid: too long
        with pytest.raises(ValidationError) as exc_info:
            TemporalEventProperties(event_date="a" * 51)
        assert "String should have at most 50 characters" in str(exc_info.value)


class TestExtractedEntitySchemaWithTemporal:
    """Tests for ExtractedEntitySchema with temporal field."""

    def test_entity_without_temporal(self):
        """Test creating entity without temporal data."""
        entity = ExtractedEntitySchema(
            name="World War I",
            entity_type="event",
            description="A global conflict",
            confidence=0.9,
        )
        assert entity.temporal is None

    def test_entity_with_temporal(self):
        """Test creating entity with temporal data."""
        entity = ExtractedEntitySchema(
            name="World War I",
            entity_type="event",
            description="A global conflict",
            confidence=0.9,
            temporal=TemporalEventProperties(
                temporal_expression="1914-1918",
                event_date="1914",
                end_date="1918",
                is_approximate=False,
            ),
        )
        assert entity.temporal is not None
        assert entity.temporal.event_date == "1914"
        assert entity.temporal.end_date == "1918"

    def test_entity_with_dict_temporal(self):
        """Test creating entity with temporal as dict."""
        entity = ExtractedEntitySchema(
            name="Battle of Waterloo",
            entity_type="event",
            confidence=0.95,
            temporal={
                "temporal_expression": "June 18, 1815",
                "event_date": "1815-06-18",
                "is_approximate": False,
            },
        )
        assert entity.temporal is not None
        assert entity.temporal.event_date == "1815-06-18"
        assert entity.temporal.temporal_expression == "June 18, 1815"

    def test_entity_with_sequence_only(self):
        """Test entity with only sequence position."""
        entity = ExtractedEntitySchema(
            name="First event",
            entity_type="event",
            confidence=0.8,
            temporal=TemporalEventProperties(
                sequence_position=1,
            ),
        )
        assert entity.temporal.sequence_position == 1
        assert entity.temporal.event_date is None


class TestTemporalRelationshipTypes:
    """Tests for temporal relationship type detection."""

    @pytest.mark.parametrize(
        "rel_type,expected",
        [
            ("precedes", True),
            ("follows", True),
            ("during", True),
            ("overlaps", True),
            ("causes", True),
            ("concurrent", True),
            ("PRECEDES", True),
            ("FOLLOWS", True),
            ("  precedes  ", True),
            ("related_to", False),
            ("loves", False),
            ("implements", False),
            ("contains", False),
            ("unknown", False),
        ],
    )
    def test_is_temporal_relationship_type(self, rel_type: str, expected: bool):
        """Test temporal relationship type detection."""
        assert is_temporal_relationship_type(rel_type) == expected

    def test_temporal_relationship_types_in_schema(self):
        """Test that temporal relationship types are in the schema."""
        # Get the literal values from the type hint
        # RelationshipTypeLiteral is a Literal type, we can check its args
        import typing

        from kg_builder.extraction.schemas import RelationshipTypeLiteral

        args = typing.get_args(RelationshipTypeLiteral)

        temporal_types = {"precedes", "follows", "during", "overlaps", "causes", "concurrent"}
        for temp_type in temporal_types:
            assert temp_type in args, f"Missing temporal relationship type: {temp_type}"
