"""
Unit tests for ExtractedEntity canonical/alias columns.

Tests the is_alias_of and is_canonical columns and relationships
added for entity consolidation support.
"""

from uuid import uuid4

import pytest

from kg_builder.models.extracted_entity import ExtractedEntity, EntityType, ExtractionMethod


@pytest.mark.unit
def test_entity_default_is_canonical():
    """New entities should be canonical by default."""
    entity = ExtractedEntity(
        tenant_id=uuid4(),
        source_page_id=uuid4(),
        entity_type=EntityType.CONCEPT.value,  # Use .value for string column
        name="Test Entity",
        extraction_method=ExtractionMethod.LLM_OLLAMA,
    )
    assert entity.is_canonical is True
    assert entity.is_alias_of is None


@pytest.mark.unit
def test_entity_can_reference_canonical():
    """Entity can be marked as alias of another."""
    canonical_id = uuid4()
    entity = ExtractedEntity(
        tenant_id=uuid4(),
        source_page_id=uuid4(),
        entity_type=EntityType.CONCEPT.value,  # Use .value for string column
        name="Alias Entity",
        extraction_method=ExtractionMethod.LLM_OLLAMA,
        is_alias_of=canonical_id,
        is_canonical=False,
    )
    assert entity.is_alias_of == canonical_id
    assert entity.is_canonical is False


@pytest.mark.unit
def test_entity_is_canonical_explicitly_true():
    """Entity can be explicitly marked as canonical."""
    entity = ExtractedEntity(
        tenant_id=uuid4(),
        source_page_id=uuid4(),
        entity_type=EntityType.PERSON.value,  # Use .value for string column
        name="Canonical Person",
        extraction_method=ExtractionMethod.SCHEMA_ORG,
        is_canonical=True,
    )
    assert entity.is_canonical is True
    assert entity.is_alias_of is None


@pytest.mark.unit
def test_entity_alias_with_canonical_set():
    """Alias entity with both is_alias_of and is_canonical=False."""
    canonical_id = uuid4()
    tenant_id = uuid4()
    page_id = uuid4()

    # Create canonical entity
    canonical_entity = ExtractedEntity(
        id=canonical_id,
        tenant_id=tenant_id,
        source_page_id=page_id,
        entity_type=EntityType.ORGANIZATION.value,  # Use .value for string column
        name="Microsoft Corporation",
        extraction_method=ExtractionMethod.SCHEMA_ORG,
        is_canonical=True,
    )

    # Create alias entity
    alias_entity = ExtractedEntity(
        tenant_id=tenant_id,
        source_page_id=page_id,
        entity_type=EntityType.ORGANIZATION.value,  # Use .value for string column
        name="Microsoft Corp.",
        extraction_method=ExtractionMethod.LLM_CLAUDE,
        is_alias_of=canonical_id,
        is_canonical=False,
    )

    assert canonical_entity.is_canonical is True
    assert canonical_entity.is_alias_of is None

    assert alias_entity.is_canonical is False
    assert alias_entity.is_alias_of == canonical_id


@pytest.mark.unit
def test_entity_has_canonical_entity_relationship():
    """ExtractedEntity model has canonical_entity relationship attribute."""
    entity = ExtractedEntity(
        tenant_id=uuid4(),
        source_page_id=uuid4(),
        entity_type=EntityType.CONCEPT.value,  # Use .value for string column
        name="Test",
        extraction_method=ExtractionMethod.PATTERN,
    )
    # Relationship attributes should exist (though may be None/empty)
    assert hasattr(entity, "canonical_entity")
    assert hasattr(entity, "alias_entities")


@pytest.mark.unit
def test_entity_type_enum_values():
    """Verify entity type enum includes all expected values."""
    expected_types = {
        "person",
        "organization",
        "location",
        "event",
        "product",
        "concept",
        "document",
        "date",
        "custom",
        "function",
        "class",
        "module",
        "pattern",
        "example",
        "parameter",
        "return_type",
        "exception",
    }
    actual_types = {e.value for e in EntityType}
    assert expected_types == actual_types


@pytest.mark.unit
def test_extraction_method_enum_values():
    """Verify extraction method enum includes all expected values."""
    expected_methods = {
        "schema_org",
        "open_graph",
        "llm_claude",
        "llm_ollama",
        "llm_openai",  # Added for OpenAI GPT extraction
        "pattern",
        "spacy",
        "hybrid",
    }
    actual_methods = {e.value for e in ExtractionMethod}
    assert expected_methods == actual_methods
