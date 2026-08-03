"""
Unit tests for extraction prompts module.

Tests DocumentationType enum, prompt selection functions,
and prompt content verification.

Note: This module imports directly from the module files to avoid
loading the full app context with database dependencies. This enables
running these pure unit tests without Docker/database setup.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

# Direct import from prompts.py file to avoid __init__.py chain
# that pulls in database-dependent modules
_prompts_path = Path(__file__).parent.parent.parent.parent / "src" / "kg_builder" / "extraction" / "prompts.py"
_prompts_spec = spec_from_file_location("extraction_prompts", _prompts_path)
_prompts = module_from_spec(_prompts_spec)
_prompts_spec.loader.exec_module(_prompts)

# Extract the items we need from prompts module
DocumentationType = _prompts.DocumentationType
SYSTEM_PROMPT_BASE = _prompts.SYSTEM_PROMPT_BASE
SYSTEM_PROMPT_API_REFERENCE = _prompts.SYSTEM_PROMPT_API_REFERENCE
SYSTEM_PROMPT_TUTORIAL = _prompts.SYSTEM_PROMPT_TUTORIAL
SYSTEM_PROMPT_CONCEPTUAL = _prompts.SYSTEM_PROMPT_CONCEPTUAL
SYSTEM_PROMPT_EXAMPLE_CODE = _prompts.SYSTEM_PROMPT_EXAMPLE_CODE
get_system_prompt = _prompts.get_system_prompt
build_user_prompt = _prompts.build_user_prompt
get_entity_types = _prompts.get_entity_types
get_relationship_types = _prompts.get_relationship_types

# Direct import from schemas.py file as well
_schemas_path = Path(__file__).parent.parent.parent.parent / "src" / "kg_builder" / "extraction" / "schemas.py"
_schemas_spec = spec_from_file_location("extraction_schemas", _schemas_path)
_schemas = module_from_spec(_schemas_spec)
_schemas_spec.loader.exec_module(_schemas)

EntityTypeLiteral = _schemas.EntityTypeLiteral
RelationshipTypeLiteral = _schemas.RelationshipTypeLiteral


# =============================================================================
# DocumentationType Enum Tests
# =============================================================================


class TestDocumentationType:
    """Tests for the DocumentationType enum."""

    def test_enum_values(self):
        """Test that all expected enum values exist."""
        assert DocumentationType.API_REFERENCE.value == "api_reference"
        assert DocumentationType.TUTORIAL.value == "tutorial"
        assert DocumentationType.CONCEPTUAL.value == "conceptual"
        assert DocumentationType.EXAMPLE_CODE.value == "example_code"
        assert DocumentationType.GENERAL.value == "general"

    def test_enum_count(self):
        """Test that we have exactly 5 documentation types."""
        assert len(DocumentationType) == 5

    def test_enum_is_string_enum(self):
        """Test that DocumentationType is a string enum."""
        for doc_type in DocumentationType:
            assert isinstance(doc_type.value, str)
            # String enum should be usable as string
            assert str(doc_type.value) == doc_type.value


# =============================================================================
# get_system_prompt Tests
# =============================================================================


class TestGetSystemPrompt:
    """Tests for the get_system_prompt function."""

    def test_get_system_prompt_api_reference(self):
        """Test API reference prompt selection."""
        prompt = get_system_prompt(DocumentationType.API_REFERENCE)
        assert prompt == SYSTEM_PROMPT_API_REFERENCE
        assert "API Reference Focus" in prompt

    def test_get_system_prompt_tutorial(self):
        """Test tutorial prompt selection."""
        prompt = get_system_prompt(DocumentationType.TUTORIAL)
        assert prompt == SYSTEM_PROMPT_TUTORIAL
        assert "Tutorial Focus" in prompt

    def test_get_system_prompt_conceptual(self):
        """Test conceptual prompt selection."""
        prompt = get_system_prompt(DocumentationType.CONCEPTUAL)
        assert prompt == SYSTEM_PROMPT_CONCEPTUAL
        assert "Conceptual Documentation Focus" in prompt

    def test_get_system_prompt_example_code(self):
        """Test example code prompt selection."""
        prompt = get_system_prompt(DocumentationType.EXAMPLE_CODE)
        assert prompt == SYSTEM_PROMPT_EXAMPLE_CODE
        assert "Example Code Focus" in prompt

    def test_get_system_prompt_general(self):
        """Test general prompt selection (default)."""
        prompt = get_system_prompt(DocumentationType.GENERAL)
        assert prompt == SYSTEM_PROMPT_BASE

    def test_get_system_prompt_default(self):
        """Test default prompt selection when no type specified."""
        prompt = get_system_prompt()
        assert prompt == SYSTEM_PROMPT_BASE

    def test_type_specific_prompts_extend_base(self):
        """Test that type-specific prompts contain base prompt content."""
        base = SYSTEM_PROMPT_BASE

        assert base in SYSTEM_PROMPT_API_REFERENCE
        assert base in SYSTEM_PROMPT_TUTORIAL
        assert base in SYSTEM_PROMPT_CONCEPTUAL
        assert base in SYSTEM_PROMPT_EXAMPLE_CODE


# =============================================================================
# build_user_prompt Tests
# =============================================================================


class TestBuildUserPrompt:
    """Tests for the build_user_prompt function."""

    def test_includes_content(self):
        """Test that build_user_prompt includes the content."""
        content = "class DomainEvent(BaseModel): pass"
        prompt = build_user_prompt(
            content=content,
            page_url="https://example.com/docs",
        )
        assert content in prompt

    def test_includes_url(self):
        """Test that build_user_prompt includes the page URL."""
        page_url = "https://docs.example.com/api/events"
        prompt = build_user_prompt(
            content="some content",
            page_url=page_url,
        )
        assert page_url in prompt

    def test_includes_doc_type_hint(self):
        """Test that build_user_prompt includes document type hint."""
        prompt = build_user_prompt(
            content="some content",
            page_url="https://example.com",
            doc_type=DocumentationType.API_REFERENCE,
        )
        assert "Document Type: api_reference" in prompt

    def test_includes_tutorial_type_hint(self):
        """Test document type hint for tutorial."""
        prompt = build_user_prompt(
            content="some content",
            page_url="https://example.com",
            doc_type=DocumentationType.TUTORIAL,
        )
        assert "Document Type: tutorial" in prompt

    def test_includes_additional_context(self):
        """Test that build_user_prompt includes additional context."""
        context = "This is documentation for eventsource-py library"
        prompt = build_user_prompt(
            content="some content",
            page_url="https://example.com",
            additional_context=context,
        )
        assert f"Additional Context: {context}" in prompt

    def test_no_doc_type_hint_when_none(self):
        """Test that no type hint is added when doc_type is None."""
        prompt = build_user_prompt(
            content="some content",
            page_url="https://example.com",
            doc_type=None,
        )
        assert "Document Type:" not in prompt

    def test_no_context_section_when_none(self):
        """Test that no context section is added when additional_context is None."""
        prompt = build_user_prompt(
            content="some content",
            page_url="https://example.com",
            additional_context=None,
        )
        assert "Additional Context:" not in prompt

    def test_includes_extraction_instructions(self):
        """Test that prompt includes extraction instructions."""
        prompt = build_user_prompt(
            content="some content",
            page_url="https://example.com",
        )
        assert "entities" in prompt.lower()
        assert "relationships" in prompt.lower()
        assert "confidence" in prompt.lower()

    def test_content_is_delimited(self):
        """Test that content is properly delimited in the prompt."""
        content = "test content here"
        prompt = build_user_prompt(
            content=content,
            page_url="https://example.com",
        )
        # Content should be between --- markers
        assert "---\n" + content + "\n---" in prompt


# =============================================================================
# Entity and Relationship Types Coverage Tests
# =============================================================================


class TestPromptEntityTypeCoverage:
    """Tests to verify all entity types are mentioned in prompts."""

    def test_base_prompt_mentions_core_entity_types(self):
        """Test that base prompt mentions core entity types."""
        prompt = SYSTEM_PROMPT_BASE.lower()

        # Core code entity types
        assert "function" in prompt
        assert "class" in prompt
        assert "module" in prompt
        assert "exception" in prompt

        # Conceptual entity types
        assert "concept" in prompt
        assert "pattern" in prompt

        # Documentation entity types
        assert "example" in prompt

    def test_base_prompt_mentions_function_properties(self):
        """Test that base prompt mentions function properties."""
        prompt = SYSTEM_PROMPT_BASE.lower()

        assert "signature" in prompt
        assert "parameter" in prompt
        assert "return_type" in prompt or "return type" in prompt
        assert "async" in prompt
        assert "docstring" in prompt

    def test_base_prompt_mentions_class_properties(self):
        """Test that base prompt mentions class properties."""
        prompt = SYSTEM_PROMPT_BASE.lower()

        assert "base_class" in prompt or "base class" in prompt or "inherit" in prompt
        assert "method" in prompt
        assert "attribute" in prompt
        assert "abstract" in prompt

    def test_base_prompt_mentions_module_properties(self):
        """Test that base prompt mentions module properties."""
        prompt = SYSTEM_PROMPT_BASE.lower()

        assert "module" in prompt
        assert "package" in prompt or "submodule" in prompt

    def test_base_prompt_mentions_pattern_properties(self):
        """Test that base prompt mentions pattern properties."""
        prompt = SYSTEM_PROMPT_BASE.lower()

        assert "pattern" in prompt
        assert "category" in prompt or "problem" in prompt or "solution" in prompt


class TestPromptRelationshipTypeCoverage:
    """Tests to verify all relationship types are mentioned in prompts."""

    def test_base_prompt_mentions_code_relationships(self):
        """Test that base prompt mentions code structure relationships."""
        prompt = SYSTEM_PROMPT_BASE.lower()

        assert "extends" in prompt
        assert "implements" in prompt
        assert "contains" in prompt
        assert "part_of" in prompt or "part of" in prompt

    def test_base_prompt_mentions_function_relationships(self):
        """Test that base prompt mentions function relationships."""
        prompt = SYSTEM_PROMPT_BASE.lower()

        assert "calls" in prompt
        assert "returns" in prompt
        assert "accepts" in prompt
        assert "raises" in prompt

    def test_base_prompt_mentions_dependency_relationships(self):
        """Test that base prompt mentions dependency relationships."""
        prompt = SYSTEM_PROMPT_BASE.lower()

        assert "uses" in prompt
        assert "depends_on" in prompt or "depends on" in prompt
        assert "imports" in prompt
        assert "requires" in prompt

    def test_base_prompt_mentions_documentation_relationships(self):
        """Test that base prompt mentions documentation relationships."""
        prompt = SYSTEM_PROMPT_BASE.lower()

        assert "documented_in" in prompt or "documented in" in prompt
        assert "example_of" in prompt or "example of" in prompt or "demonstrates" in prompt


# =============================================================================
# get_entity_types and get_relationship_types Tests
# =============================================================================


class TestGetEntityTypes:
    """Tests for the get_entity_types function."""

    def test_returns_list(self):
        """Test that get_entity_types returns a list."""
        entity_types = get_entity_types()
        assert isinstance(entity_types, list)

    def test_contains_core_entity_types(self):
        """Test that core entity types are included."""
        entity_types = get_entity_types()

        assert "function" in entity_types
        assert "class" in entity_types
        assert "module" in entity_types
        assert "pattern" in entity_types
        assert "example" in entity_types
        assert "exception" in entity_types
        assert "concept" in entity_types

    def test_returns_copy(self):
        """Test that get_entity_types returns a copy, not the original."""
        types1 = get_entity_types()
        types2 = get_entity_types()

        # Should be equal but not the same object
        assert types1 == types2
        assert types1 is not types2

        # Modifying one shouldn't affect the other
        types1.append("test")
        assert "test" not in types2

    def test_all_types_are_valid_literals(self):
        """Test that all returned types are valid EntityTypeLiteral values."""
        entity_types = get_entity_types()

        # Get the valid literal values from the schema
        from typing import get_args
        valid_types = set(get_args(EntityTypeLiteral))

        for entity_type in entity_types:
            assert entity_type in valid_types, f"{entity_type} is not a valid EntityTypeLiteral"


class TestGetRelationshipTypes:
    """Tests for the get_relationship_types function."""

    def test_returns_list(self):
        """Test that get_relationship_types returns a list."""
        rel_types = get_relationship_types()
        assert isinstance(rel_types, list)

    def test_contains_core_relationship_types(self):
        """Test that core relationship types are included."""
        rel_types = get_relationship_types()

        # Code structure
        assert "extends" in rel_types
        assert "implements" in rel_types
        assert "contains" in rel_types

        # Function relationships
        assert "calls" in rel_types
        assert "returns" in rel_types
        assert "raises" in rel_types

        # Dependencies
        assert "uses" in rel_types
        assert "depends_on" in rel_types
        assert "imports" in rel_types

        # Documentation
        assert "documented_in" in rel_types
        assert "example_of" in rel_types

    def test_returns_copy(self):
        """Test that get_relationship_types returns a copy."""
        types1 = get_relationship_types()
        types2 = get_relationship_types()

        assert types1 == types2
        assert types1 is not types2

        types1.append("test")
        assert "test" not in types2

    def test_all_types_are_valid_literals(self):
        """Test that all returned types are valid RelationshipTypeLiteral values."""
        rel_types = get_relationship_types()

        from typing import get_args
        valid_types = set(get_args(RelationshipTypeLiteral))

        for rel_type in rel_types:
            assert rel_type in valid_types, f"{rel_type} is not a valid RelationshipTypeLiteral"


# =============================================================================
# Prompt Content Quality Tests
# =============================================================================


class TestPromptContentQuality:
    """Tests for prompt content quality and structure."""

    def test_base_prompt_has_json_format_instructions(self):
        """Test that base prompt includes JSON output format instructions."""
        prompt = SYSTEM_PROMPT_BASE.lower()

        assert "json" in prompt
        assert "entities" in prompt
        assert "relationships" in prompt

    def test_base_prompt_has_confidence_instructions(self):
        """Test that base prompt includes confidence scoring instructions."""
        prompt = SYSTEM_PROMPT_BASE.lower()

        assert "confidence" in prompt
        assert "0.7" in prompt or "0.0" in prompt  # Mentions threshold or range

    def test_api_reference_prompt_has_api_focus(self):
        """Test API reference prompt has appropriate focus areas."""
        prompt = SYSTEM_PROMPT_API_REFERENCE.lower()

        assert "signature" in prompt
        assert "parameter" in prompt
        assert "return" in prompt
        assert "api" in prompt

    def test_tutorial_prompt_has_learning_focus(self):
        """Test tutorial prompt has appropriate focus areas."""
        prompt = SYSTEM_PROMPT_TUTORIAL.lower()

        assert "concept" in prompt
        assert "example" in prompt
        assert "demonstrate" in prompt or "demonstrat" in prompt

    def test_conceptual_prompt_has_design_focus(self):
        """Test conceptual prompt has appropriate focus areas."""
        prompt = SYSTEM_PROMPT_CONCEPTUAL.lower()

        assert "concept" in prompt
        assert "pattern" in prompt
        assert "design" in prompt
        assert "architecture" in prompt or "architectural" in prompt

    def test_example_code_prompt_has_code_focus(self):
        """Test example code prompt has appropriate focus areas."""
        prompt = SYSTEM_PROMPT_EXAMPLE_CODE.lower()

        assert "example" in prompt
        assert "demonstrate" in prompt or "demonstrat" in prompt
        assert "code" in prompt

    def test_extraction_guidelines_present(self):
        """Test that extraction guidelines are present in base prompt."""
        prompt = SYSTEM_PROMPT_BASE

        assert "Extraction Guidelines" in prompt
        assert "Be Precise" in prompt or "precise" in prompt.lower()

    def test_relationship_direction_documented(self):
        """Test that relationship direction is documented."""
        prompt = SYSTEM_PROMPT_BASE.lower()

        assert "source" in prompt
        assert "target" in prompt
        assert "direction" in prompt or "->" in prompt


# =============================================================================
# Integration with OllamaExtractionService Tests
# =============================================================================

# Try to import OllamaExtractionService, skip tests if database dependencies unavailable
try:
    from kg_builder.extraction.ollama_extractor import OllamaExtractionService
    _OLLAMA_SERVICE_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _OLLAMA_SERVICE_AVAILABLE = False
    OllamaExtractionService = None


@pytest.mark.skipif(not _OLLAMA_SERVICE_AVAILABLE, reason="OllamaExtractionService requires database dependencies")
class TestPromptsIntegrationWithOllamaService:
    """Tests verifying prompts work correctly with OllamaExtractionService.

    These tests require the full application context with database dependencies.
    They will be skipped when running in isolation without database setup.
    """

    def test_service_uses_get_system_prompt(self):
        """Test that OllamaExtractionService imports and uses get_system_prompt."""
        # Create service with specific doc type
        service = OllamaExtractionService(
            base_url="http://localhost:11434",
            model="test-model",
            doc_type=DocumentationType.API_REFERENCE,
        )

        # Verify the service's _get_system_prompt returns the expected prompt
        prompt = service._get_system_prompt()
        assert "API Reference Focus" in prompt

    def test_service_uses_build_user_prompt(self):
        """Test that OllamaExtractionService uses build_user_prompt."""
        service = OllamaExtractionService(
            base_url="http://localhost:11434",
            model="test-model",
            doc_type=DocumentationType.TUTORIAL,
        )

        content = "test content"
        page_url = "https://example.com"

        prompt = service._build_prompt(content, page_url)

        assert content in prompt
        assert page_url in prompt
        assert "Document Type: tutorial" in prompt

    def test_service_accepts_doc_type_override(self):
        """Test that OllamaExtractionService accepts doc_type override in _build_prompt."""
        service = OllamaExtractionService(
            base_url="http://localhost:11434",
            model="test-model",
            doc_type=DocumentationType.GENERAL,
        )

        # Override with API_REFERENCE
        prompt = service._build_prompt(
            content="test",
            page_url="https://example.com",
            doc_type=DocumentationType.API_REFERENCE,
        )

        assert "Document Type: api_reference" in prompt

    def test_service_accepts_additional_context(self):
        """Test that OllamaExtractionService passes additional_context."""
        service = OllamaExtractionService(
            base_url="http://localhost:11434",
            model="test-model",
        )

        context = "This is for the eventsource-py library"
        prompt = service._build_prompt(
            content="test",
            page_url="https://example.com",
            additional_context=context,
        )

        assert context in prompt
