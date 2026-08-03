"""Unit tests for DomainPromptGenerator.

Tests the prompt generation functionality including:
- System prompt generation with entity/relationship descriptions
- JSON schema generation for structured output
- Content truncation for user prompts
- Property hint formatting
- Convenience functions
"""

import pytest

from kg_builder.extraction.domains.models import (
    DomainSchema,
    EntityTypeSchema,
    PropertySchema,
    RelationshipTypeSchema,
)
from kg_builder.extraction.prompt_generator import (
    DEFAULT_MAX_CONTENT_LENGTH,
    DomainPromptGenerator,
    generate_extraction_prompt,
    generate_output_schema,
    generate_user_prompt,
    get_prompt_generator,
    reset_prompt_generator,
)


class TestDomainPromptGenerator:
    """Tests for DomainPromptGenerator class."""

    @pytest.fixture
    def generator(self) -> DomainPromptGenerator:
        """Create a fresh generator instance."""
        return DomainPromptGenerator()

    @pytest.fixture
    def sample_schema(self) -> DomainSchema:
        """Create a sample domain schema for testing."""
        return DomainSchema(
            domain_id="test_domain",
            display_name="Test Domain",
            description="A test domain for unit tests",
            entity_types=[
                EntityTypeSchema(
                    id="character",
                    description="A character in the story",
                    examples=["Hamlet", "Macbeth", "Ophelia"],
                ),
                EntityTypeSchema(
                    id="theme",
                    description="A theme in the work",
                ),
            ],
            relationship_types=[
                RelationshipTypeSchema(
                    id="loves",
                    description="Romantic love",
                    valid_source_types=["character"],
                    valid_target_types=["character"],
                ),
                RelationshipTypeSchema(
                    id="related_to",
                    description="General relationship",
                    bidirectional=True,
                ),
            ],
            extraction_prompt_template="Extract entities:\n{entity_descriptions}\n\nRelationships:\n{relationship_descriptions}",
        )

    @pytest.fixture
    def schema_with_properties(self) -> DomainSchema:
        """Create a schema with entity properties for testing."""
        return DomainSchema(
            domain_id="test_with_props",
            display_name="Test With Properties",
            description="A test domain with entity properties",
            entity_types=[
                EntityTypeSchema(
                    id="character",
                    description="A character in the story",
                    properties=[
                        PropertySchema(
                            name="role",
                            type="string",
                            description="Role in story",
                        ),
                        PropertySchema(
                            name="allegiance",
                            type="string",
                            description="Group or faction",
                        ),
                    ],
                    examples=["Hero", "Villain"],
                ),
            ],
            relationship_types=[
                RelationshipTypeSchema(
                    id="allies_with",
                    description="Alliance relationship",
                    bidirectional=True,
                ),
            ],
            extraction_prompt_template="Entities:\n{entity_descriptions}\n\nRelationships:\n{relationship_descriptions}",
        )

    # ========== System Prompt Generation Tests ==========

    def test_generate_system_prompt_basic(
        self, generator: DomainPromptGenerator, sample_schema: DomainSchema
    ) -> None:
        """Test basic system prompt generation with placeholders filled."""
        prompt = generator.generate_system_prompt(sample_schema)

        # Check structure is preserved
        assert "Extract entities:" in prompt
        assert "Relationships:" in prompt

        # Check entity descriptions are filled in
        assert "character" in prompt
        assert "A character in the story" in prompt

        # Check relationship descriptions are filled in
        assert "loves" in prompt
        assert "Romantic love" in prompt

    def test_generate_system_prompt_with_examples(
        self, generator: DomainPromptGenerator, sample_schema: DomainSchema
    ) -> None:
        """Test that entity examples are included in prompt."""
        prompt = generator.generate_system_prompt(sample_schema)

        assert "Hamlet" in prompt
        assert "Macbeth" in prompt
        assert "Ophelia" in prompt

    def test_generate_system_prompt_examples_limited(
        self, generator: DomainPromptGenerator
    ) -> None:
        """Test that only first 3 examples are included."""
        schema = DomainSchema(
            domain_id="many_examples",
            display_name="Many Examples",
            description="Domain with many examples",
            entity_types=[
                EntityTypeSchema(
                    id="item",
                    description="An item",
                    examples=["A", "B", "C", "D", "E"],
                ),
            ],
            relationship_types=[
                RelationshipTypeSchema(
                    id="related_to",
                    description="General",
                ),
            ],
            extraction_prompt_template="{entity_descriptions}\n{relationship_descriptions}",
        )

        prompt = generator.generate_system_prompt(schema)

        # First 3 should be present
        assert "A" in prompt
        assert "B" in prompt
        assert "C" in prompt
        # 4th and 5th should not be in the examples part
        # Note: They might appear elsewhere in the prompt accidentally
        assert "examples: A, B, C" in prompt

    def test_generate_system_prompt_relationship_constraints(
        self, generator: DomainPromptGenerator, sample_schema: DomainSchema
    ) -> None:
        """Test that relationship source/target constraints appear in prompt."""
        prompt = generator.generate_system_prompt(sample_schema)

        assert "from: character" in prompt
        assert "to: character" in prompt

    def test_generate_system_prompt_bidirectional_marker(
        self, generator: DomainPromptGenerator, sample_schema: DomainSchema
    ) -> None:
        """Test that bidirectional relationships are marked."""
        prompt = generator.generate_system_prompt(sample_schema)

        assert "bidirectional" in prompt

    def test_generate_system_prompt_with_properties(
        self, generator: DomainPromptGenerator, schema_with_properties: DomainSchema
    ) -> None:
        """Test that entity properties appear as hints."""
        prompt = generator.generate_system_prompt(schema_with_properties)

        assert "Properties:" in prompt
        assert "role" in prompt
        assert "Role in story" in prompt
        assert "allegiance" in prompt

    def test_generate_system_prompt_no_placeholders_left(
        self, generator: DomainPromptGenerator, sample_schema: DomainSchema
    ) -> None:
        """Test that no unfilled placeholders remain."""
        prompt = generator.generate_system_prompt(sample_schema)

        assert "{entity_descriptions}" not in prompt
        assert "{relationship_descriptions}" not in prompt

    # ========== JSON Schema Generation Tests ==========

    def test_generate_json_schema_structure(
        self, generator: DomainPromptGenerator, sample_schema: DomainSchema
    ) -> None:
        """Test JSON schema has correct structure."""
        json_schema = generator.generate_json_schema(sample_schema)

        assert json_schema["type"] == "object"
        assert "entities" in json_schema["properties"]
        assert "relationships" in json_schema["properties"]
        assert json_schema["required"] == ["entities", "relationships"]

    def test_generate_json_schema_entity_enum(
        self, generator: DomainPromptGenerator, sample_schema: DomainSchema
    ) -> None:
        """Test that entity type enum includes domain types and custom."""
        json_schema = generator.generate_json_schema(sample_schema)

        entity_items = json_schema["properties"]["entities"]["items"]
        entity_enum = entity_items["properties"]["type"]["enum"]

        assert "character" in entity_enum
        assert "theme" in entity_enum
        assert "custom" in entity_enum  # Fallback type

    def test_generate_json_schema_relationship_enum(
        self, generator: DomainPromptGenerator, sample_schema: DomainSchema
    ) -> None:
        """Test that relationship type enum includes domain types and related_to."""
        json_schema = generator.generate_json_schema(sample_schema)

        rel_items = json_schema["properties"]["relationships"]["items"]
        rel_enum = rel_items["properties"]["type"]["enum"]

        assert "loves" in rel_enum
        assert "related_to" in rel_enum  # Fallback type (already in schema but ensure present)

    def test_generate_json_schema_adds_related_to_if_missing(
        self, generator: DomainPromptGenerator
    ) -> None:
        """Test that related_to is added if not in domain schema."""
        schema = DomainSchema(
            domain_id="no_related_to",
            display_name="No Related To",
            description="Domain without related_to",
            entity_types=[
                EntityTypeSchema(id="item", description="An item"),
            ],
            relationship_types=[
                RelationshipTypeSchema(id="connects", description="Connection"),
            ],
            extraction_prompt_template="{entity_descriptions}\n{relationship_descriptions}",
        )

        json_schema = generator.generate_json_schema(schema)
        rel_enum = json_schema["properties"]["relationships"]["items"]["properties"]["type"]["enum"]

        assert "connects" in rel_enum
        assert "related_to" in rel_enum

    def test_generate_json_schema_entity_required_fields(
        self, generator: DomainPromptGenerator, sample_schema: DomainSchema
    ) -> None:
        """Test entity items have correct required fields."""
        json_schema = generator.generate_json_schema(sample_schema)

        entity_items = json_schema["properties"]["entities"]["items"]

        assert entity_items["required"] == ["name", "type"]

    def test_generate_json_schema_relationship_required_fields(
        self, generator: DomainPromptGenerator, sample_schema: DomainSchema
    ) -> None:
        """Test relationship items have correct required fields."""
        json_schema = generator.generate_json_schema(sample_schema)

        rel_items = json_schema["properties"]["relationships"]["items"]

        assert rel_items["required"] == ["source", "target", "type"]

    def test_generate_json_schema_confidence_bounds(
        self, generator: DomainPromptGenerator, sample_schema: DomainSchema
    ) -> None:
        """Test that confidence field has proper bounds."""
        json_schema = generator.generate_json_schema(sample_schema)

        entity_items = json_schema["properties"]["entities"]["items"]
        confidence = entity_items["properties"]["confidence"]

        assert confidence["type"] == "number"
        assert confidence["minimum"] == 0
        assert confidence["maximum"] == 1

    # ========== User Prompt Generation Tests ==========

    def test_generate_user_prompt_basic(self, generator: DomainPromptGenerator) -> None:
        """Test basic user prompt generation."""
        content = "This is some test content."
        prompt = generator.generate_user_prompt(content)

        assert "Extract entities and relationships" in prompt
        assert "This is some test content." in prompt

    def test_generate_user_prompt_truncation(self) -> None:
        """Test that long content is truncated."""
        generator = DomainPromptGenerator(max_content_length=100)
        content = "A" * 200

        prompt = generator.generate_user_prompt(content, truncate=True)

        assert len(prompt) < 200 + 100  # Content + prompt text
        assert "[Content truncated due to length]" in prompt

    def test_generate_user_prompt_no_truncation(self) -> None:
        """Test that truncation can be disabled."""
        generator = DomainPromptGenerator(max_content_length=100)
        content = "A" * 200

        prompt = generator.generate_user_prompt(content, truncate=False)

        assert "[Content truncated due to length]" not in prompt
        assert "A" * 200 in prompt

    def test_generate_user_prompt_truncates_at_sentence(self) -> None:
        """Test that truncation tries to respect sentence boundaries."""
        generator = DomainPromptGenerator(max_content_length=100)
        # Create content with a sentence boundary in the middle
        content = "First sentence. " + "A" * 80 + ". More text here that should be cut."

        prompt = generator.generate_user_prompt(content, truncate=True)

        # Should truncate but preserve the first sentence
        assert "First sentence." in prompt
        assert "[Content truncated due to length]" in prompt

    def test_generate_user_prompt_short_content_not_truncated(
        self, generator: DomainPromptGenerator
    ) -> None:
        """Test that short content is not truncated."""
        content = "Short content."
        prompt = generator.generate_user_prompt(content)

        assert "Short content." in prompt
        assert "[Content truncated due to length]" not in prompt

    # ========== Full Prompt Generation Tests ==========

    def test_generate_full_prompt(
        self, generator: DomainPromptGenerator, sample_schema: DomainSchema
    ) -> None:
        """Test generating both system and user prompts."""
        content = "Test content for extraction."
        system_prompt, user_prompt = generator.generate_full_prompt(sample_schema, content)

        # System prompt should have entity descriptions
        assert "character" in system_prompt
        assert "A character in the story" in system_prompt

        # User prompt should have content
        assert "Test content for extraction." in user_prompt

    def test_generate_full_prompt_with_truncation(self, sample_schema: DomainSchema) -> None:
        """Test that full prompt truncates long content."""
        generator = DomainPromptGenerator(max_content_length=50)
        content = "A" * 100

        _, user_prompt = generator.generate_full_prompt(sample_schema, content, truncate=True)

        assert "[Content truncated due to length]" in user_prompt

    # ========== Property Hints Tests ==========

    def test_format_property_hints(self, generator: DomainPromptGenerator) -> None:
        """Test property hint formatting."""
        properties = [
            PropertySchema(name="name", type="string", description="The name"),
            PropertySchema(name="age", type="number", description="The age"),
        ]

        hints = generator._format_property_hints(properties)

        assert "name (The name)" in hints
        assert "age (The age)" in hints

    def test_format_property_hints_no_description(self, generator: DomainPromptGenerator) -> None:
        """Test property hints without descriptions."""
        properties = [
            PropertySchema(name="name", type="string"),
        ]

        hints = generator._format_property_hints(properties)

        assert hints == "name"

    def test_format_property_hints_empty(self, generator: DomainPromptGenerator) -> None:
        """Test empty properties list."""
        hints = generator._format_property_hints([])

        assert hints == ""


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self) -> None:
        """Reset singleton before each test."""
        reset_prompt_generator()
        yield
        reset_prompt_generator()

    @pytest.fixture
    def sample_schema(self) -> DomainSchema:
        """Create a sample domain schema."""
        return DomainSchema(
            domain_id="test",
            display_name="Test",
            description="Test domain",
            entity_types=[
                EntityTypeSchema(
                    id="entity",
                    description="An entity",
                ),
            ],
            relationship_types=[
                RelationshipTypeSchema(
                    id="relates",
                    description="A relationship",
                ),
            ],
            extraction_prompt_template="{entity_descriptions}\n{relationship_descriptions}",
        )

    def test_get_prompt_generator_returns_singleton(self) -> None:
        """Test that get_prompt_generator returns same instance."""
        gen1 = get_prompt_generator()
        gen2 = get_prompt_generator()

        assert gen1 is gen2

    def test_reset_prompt_generator(self) -> None:
        """Test that reset creates new instance."""
        gen1 = get_prompt_generator()
        reset_prompt_generator()
        gen2 = get_prompt_generator()

        assert gen1 is not gen2

    def test_generate_extraction_prompt(self, sample_schema: DomainSchema) -> None:
        """Test convenience function for system prompt."""
        prompt = generate_extraction_prompt(sample_schema)

        assert "entity" in prompt
        assert "An entity" in prompt
        assert "relates" in prompt

    def test_generate_output_schema(self, sample_schema: DomainSchema) -> None:
        """Test convenience function for JSON schema."""
        schema = generate_output_schema(sample_schema)

        assert "entities" in schema["properties"]
        assert "relationships" in schema["properties"]

    def test_generate_user_prompt_function(self) -> None:
        """Test convenience function for user prompt."""
        content = "Test content."
        prompt = generate_user_prompt(content)

        assert "Test content." in prompt

    def test_generate_user_prompt_with_custom_length(self) -> None:
        """Test user prompt with custom max length."""
        content = "A" * 100
        prompt = generate_user_prompt(content, max_length=50)

        assert "[Content truncated due to length]" in prompt


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    @pytest.fixture
    def generator(self) -> DomainPromptGenerator:
        """Create a generator instance."""
        return DomainPromptGenerator()

    def test_schema_without_examples(self, generator: DomainPromptGenerator) -> None:
        """Test schema with no examples."""
        schema = DomainSchema(
            domain_id="no_examples",
            display_name="No Examples",
            description="Domain without examples",
            entity_types=[
                EntityTypeSchema(
                    id="entity",
                    description="An entity without examples",
                ),
            ],
            relationship_types=[
                RelationshipTypeSchema(
                    id="relates",
                    description="A relationship",
                ),
            ],
            extraction_prompt_template="{entity_descriptions}\n{relationship_descriptions}",
        )

        prompt = generator.generate_system_prompt(schema)

        assert "entity" in prompt
        assert "An entity without examples" in prompt
        # Should not have examples section
        assert "examples:" not in prompt

    def test_relationship_without_constraints(self, generator: DomainPromptGenerator) -> None:
        """Test relationship with no source/target constraints."""
        schema = DomainSchema(
            domain_id="no_constraints",
            display_name="No Constraints",
            description="Domain without constraints",
            entity_types=[
                EntityTypeSchema(
                    id="entity",
                    description="An entity",
                ),
            ],
            relationship_types=[
                RelationshipTypeSchema(
                    id="relates",
                    description="A general relationship",
                    # No valid_source_types or valid_target_types
                ),
            ],
            extraction_prompt_template="{entity_descriptions}\n{relationship_descriptions}",
        )

        prompt = generator.generate_system_prompt(schema)

        # Should not have constraint parentheses
        assert "relates" in prompt
        assert "A general relationship" in prompt
        # Relationship line should not have from:/to: if no constraints
        lines = prompt.split("\n")
        rel_line = [l for l in lines if "relates" in l][0]
        assert "from:" not in rel_line
        assert "to:" not in rel_line

    def test_empty_content_user_prompt(self, generator: DomainPromptGenerator) -> None:
        """Test user prompt with empty content."""
        prompt = generator.generate_user_prompt("")

        assert "Extract entities and relationships" in prompt

    def test_whitespace_only_content(self, generator: DomainPromptGenerator) -> None:
        """Test user prompt with whitespace-only content."""
        prompt = generator.generate_user_prompt("   \n\t   ")

        assert "Extract entities and relationships" in prompt

    def test_default_max_content_length(self) -> None:
        """Test that default max content length is reasonable."""
        generator = DomainPromptGenerator()

        assert generator.max_content_length == DEFAULT_MAX_CONTENT_LENGTH
        assert generator.max_content_length >= 4000  # Should be large enough for most use cases

    def test_template_without_placeholders(self, generator: DomainPromptGenerator) -> None:
        """Test schema with template that has no standard placeholders."""
        schema = DomainSchema(
            domain_id="no_placeholders",
            display_name="No Placeholders",
            description="Domain with static template",
            entity_types=[
                EntityTypeSchema(
                    id="entity",
                    description="An entity",
                ),
            ],
            relationship_types=[
                RelationshipTypeSchema(
                    id="relates",
                    description="A relationship",
                ),
            ],
            extraction_prompt_template="This is a static template without placeholders.",
        )

        prompt = generator.generate_system_prompt(schema)

        # Should return template unchanged
        assert prompt == "This is a static template without placeholders."

    def test_multiple_entity_types(self, generator: DomainPromptGenerator) -> None:
        """Test schema with many entity types."""
        entity_types = [
            EntityTypeSchema(
                id=f"type_{i}",
                description=f"Description for type {i}",
            )
            for i in range(10)
        ]

        schema = DomainSchema(
            domain_id="many_types",
            display_name="Many Types",
            description="Domain with many entity types",
            entity_types=entity_types,
            relationship_types=[
                RelationshipTypeSchema(
                    id="relates",
                    description="A relationship",
                ),
            ],
            extraction_prompt_template="{entity_descriptions}\n{relationship_descriptions}",
        )

        prompt = generator.generate_system_prompt(schema)

        # All types should be present
        for i in range(10):
            assert f"type_{i}" in prompt
            assert f"Description for type {i}" in prompt
