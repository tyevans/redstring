"""
Unit tests for EntityType enum to string migration.

These tests verify that:
1. entity_type is stored as a string
2. Legacy enum types still work as strings
3. Domain-specific types are supported
4. original_entity_type tracking works
5. Helper properties work correctly
"""

import uuid
from datetime import datetime, timezone

import pytest

from kg_builder.models.extracted_entity import (
    EntityType,
    ExtractionMethod,
    ExtractedEntity,
)


class TestEntityTypeIsString:
    """Test that entity_type works as a string column."""

    def test_entity_type_accepts_string(self):
        """Test that entity_type can be set as a string."""
        entity = ExtractedEntity(
            tenant_id=uuid.uuid4(),
            source_page_id=uuid.uuid4(),
            entity_type="character",  # Domain-specific type
            name="Hamlet",
            normalized_name="hamlet",
            extraction_method=ExtractionMethod.LLM_OLLAMA,
            confidence_score=0.95,
        )

        assert entity.entity_type == "character"
        assert isinstance(entity.entity_type, str)

    def test_entity_type_accepts_domain_specific_types(self):
        """Test various domain-specific entity types."""
        domain_types = ["character", "theme", "plot_point", "setting", "motif"]

        for domain_type in domain_types:
            entity = ExtractedEntity(
                tenant_id=uuid.uuid4(),
                source_page_id=uuid.uuid4(),
                entity_type=domain_type,
                name=f"Test {domain_type}",
                extraction_method=ExtractionMethod.LLM_OLLAMA,
                confidence_score=0.90,
            )
            assert entity.entity_type == domain_type


class TestLegacyEntityTypesWork:
    """Test backward compatibility with legacy EntityType enum values."""

    def test_legacy_enum_value_as_string(self):
        """Test that legacy enum types work as strings."""
        entity = ExtractedEntity(
            tenant_id=uuid.uuid4(),
            source_page_id=uuid.uuid4(),
            entity_type=EntityType.PERSON.value,  # Use .value for string
            name="John Doe",
            normalized_name="john doe",
            extraction_method=ExtractionMethod.LLM_OLLAMA,
            confidence_score=0.95,
        )

        assert entity.entity_type == "person"
        assert entity.entity_type_enum == EntityType.PERSON
        assert entity.is_known_entity_type is True

    def test_all_legacy_types_work(self):
        """Test all EntityType enum values work as strings."""
        for entity_type in EntityType:
            entity = ExtractedEntity(
                tenant_id=uuid.uuid4(),
                source_page_id=uuid.uuid4(),
                entity_type=entity_type.value,
                name=f"Test {entity_type.name}",
                extraction_method=ExtractionMethod.LLM_OLLAMA,
                confidence_score=0.90,
            )
            assert entity.entity_type == entity_type.value
            assert entity.entity_type_enum == entity_type
            assert entity.is_known_entity_type is True


class TestDomainSpecificTypesReturnNoneForEnum:
    """Test that domain-specific types return None for enum property."""

    def test_unknown_type_returns_none_for_enum(self):
        """Test that domain-specific types return None for entity_type_enum."""
        entity = ExtractedEntity(
            tenant_id=uuid.uuid4(),
            source_page_id=uuid.uuid4(),
            entity_type="plot_point",  # Domain-specific, not in enum
            name="The Murder",
            normalized_name="the murder",
            extraction_method=ExtractionMethod.LLM_OLLAMA,
            confidence_score=0.85,
        )

        assert entity.entity_type == "plot_point"
        assert entity.entity_type_enum is None
        assert entity.is_known_entity_type is False

    def test_various_domain_types_return_none(self):
        """Test multiple domain-specific types."""
        domain_types = ["character", "theme", "motif", "setting", "symbol"]

        for domain_type in domain_types:
            entity = ExtractedEntity(
                tenant_id=uuid.uuid4(),
                source_page_id=uuid.uuid4(),
                entity_type=domain_type,
                name=f"Test {domain_type}",
                extraction_method=ExtractionMethod.LLM_OLLAMA,
                confidence_score=0.85,
            )

            assert entity.entity_type_enum is None
            assert entity.is_known_entity_type is False


class TestOriginalEntityTypeTracking:
    """Test tracking of original LLM entity type."""

    def test_original_entity_type_tracking(self):
        """Test that original_entity_type is stored correctly."""
        entity = ExtractedEntity(
            tenant_id=uuid.uuid4(),
            source_page_id=uuid.uuid4(),
            entity_type="person",  # Normalized type
            original_entity_type="main_character",  # LLM's original type
            name="Hamlet",
            normalized_name="hamlet",
            extraction_method=ExtractionMethod.LLM_OLLAMA,
            confidence_score=0.95,
        )

        assert entity.entity_type == "person"
        assert entity.original_entity_type == "main_character"

    def test_original_entity_type_nullable(self):
        """Test that original_entity_type can be None."""
        entity = ExtractedEntity(
            tenant_id=uuid.uuid4(),
            source_page_id=uuid.uuid4(),
            entity_type="person",
            name="John Doe",
            extraction_method=ExtractionMethod.LLM_OLLAMA,
            confidence_score=0.95,
        )

        assert entity.original_entity_type is None

    def test_original_matches_normalized(self):
        """Test when original equals normalized (both stored)."""
        entity = ExtractedEntity(
            tenant_id=uuid.uuid4(),
            source_page_id=uuid.uuid4(),
            entity_type="organization",
            original_entity_type="organization",  # Same as normalized
            name="Acme Corp",
            extraction_method=ExtractionMethod.LLM_OLLAMA,
            confidence_score=0.95,
        )

        assert entity.entity_type == "organization"
        assert entity.original_entity_type == "organization"


class TestEntityTypeEnumClassMethods:
    """Test EntityType enum class methods."""

    def test_is_valid_with_known_types(self):
        """Test is_valid returns True for known types."""
        for entity_type in EntityType:
            assert EntityType.is_valid(entity_type.value) is True

    def test_is_valid_with_unknown_types(self):
        """Test is_valid returns False for unknown types."""
        unknown_types = ["character", "theme", "plot_point", "invalid", "xyz"]
        for unknown_type in unknown_types:
            assert EntityType.is_valid(unknown_type) is False

    def test_is_valid_case_insensitive(self):
        """Test is_valid handles case-insensitivity."""
        assert EntityType.is_valid("PERSON") is True
        assert EntityType.is_valid("Person") is True
        assert EntityType.is_valid("person") is True

    def test_get_or_none_with_known_types(self):
        """Test get_or_none returns enum for known types."""
        assert EntityType.get_or_none("person") == EntityType.PERSON
        assert EntityType.get_or_none("organization") == EntityType.ORGANIZATION
        assert EntityType.get_or_none("function") == EntityType.FUNCTION

    def test_get_or_none_with_unknown_types(self):
        """Test get_or_none returns None for unknown types."""
        assert EntityType.get_or_none("character") is None
        assert EntityType.get_or_none("theme") is None
        assert EntityType.get_or_none("invalid") is None

    def test_get_or_none_case_insensitive(self):
        """Test get_or_none handles case-insensitivity."""
        assert EntityType.get_or_none("PERSON") == EntityType.PERSON
        assert EntityType.get_or_none("Person") == EntityType.PERSON


class TestEntityRepr:
    """Test ExtractedEntity __repr__ method."""

    def test_repr_with_string_type(self):
        """Test __repr__ works with string entity type."""
        entity = ExtractedEntity(
            id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
            tenant_id=uuid.uuid4(),
            source_page_id=uuid.uuid4(),
            entity_type="character",
            name="Hamlet",
            extraction_method=ExtractionMethod.LLM_OLLAMA,
            confidence_score=0.95,
        )

        repr_str = repr(entity)
        assert "12345678-1234-5678-1234-567812345678" in repr_str
        assert "Hamlet" in repr_str
        assert "character" in repr_str

    def test_repr_with_legacy_type(self):
        """Test __repr__ works with legacy entity type."""
        entity = ExtractedEntity(
            id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
            tenant_id=uuid.uuid4(),
            source_page_id=uuid.uuid4(),
            entity_type="person",
            name="John Doe",
            extraction_method=ExtractionMethod.LLM_OLLAMA,
            confidence_score=0.95,
        )

        repr_str = repr(entity)
        assert "person" in repr_str
        assert "John Doe" in repr_str
