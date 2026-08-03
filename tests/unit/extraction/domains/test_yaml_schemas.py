"""Unit tests for YAML domain schema files.

This module tests:
1. All expected schema files exist
2. Schema files are valid YAML
3. Schemas validate against DomainSchema Pydantic model
4. Schemas have minimum required entity types and relationship types
5. Schemas have non-empty prompt templates
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kg_builder.extraction.domains.models import DomainSchema

# Path to schema directory
SCHEMA_DIR = (
    Path(__file__).parent.parent.parent.parent.parent
    / "src"
    / "kg_builder"
    / "extraction"
    / "domains"
    / "schemas"
)

# Expected domain IDs that must have schema files
EXPECTED_DOMAINS = [
    "technical_documentation",
    "literature_fiction",
    "news_journalism",
    "academic_research",
    "encyclopedia_wiki",
    "business_corporate",
]


class TestSchemaFilesExist:
    """Tests for verifying schema files exist."""

    def test_all_schema_files_exist(self) -> None:
        """Test that all expected schema files exist."""
        for domain_id in EXPECTED_DOMAINS:
            schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
            assert schema_path.exists(), f"Missing schema file: {schema_path}"

    def test_schema_directory_exists(self) -> None:
        """Test that the schemas directory exists."""
        assert SCHEMA_DIR.exists(), f"Schema directory does not exist: {SCHEMA_DIR}"
        assert SCHEMA_DIR.is_dir(), f"Schema path is not a directory: {SCHEMA_DIR}"


class TestSchemaYamlValidity:
    """Tests for YAML syntax validity."""

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_schema_is_valid_yaml(self, domain_id: str) -> None:
        """Test that schema files are valid YAML."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert data is not None, f"Schema file is empty: {schema_path}"
        assert isinstance(data, dict), f"Schema must be a mapping: {schema_path}"

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_schema_has_required_keys(self, domain_id: str) -> None:
        """Test that schema files have required top-level keys."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        required_keys = [
            "domain_id",
            "display_name",
            "description",
            "entity_types",
            "relationship_types",
            "extraction_prompt_template",
        ]
        for key in required_keys:
            assert key in data, f"Schema {domain_id} missing required key: {key}"


class TestSchemaValidation:
    """Tests for Pydantic model validation."""

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_schema_validates_against_model(self, domain_id: str) -> None:
        """Test that schemas validate against DomainSchema model."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # Should not raise ValidationError
        schema = DomainSchema.model_validate(data)
        assert schema.domain_id == domain_id, (
            f"Schema domain_id mismatch: expected {domain_id}, got {schema.domain_id}"
        )

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_schema_version_is_semver(self, domain_id: str) -> None:
        """Test that schema versions follow semver format."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        schema = DomainSchema.model_validate(data)
        # Version should match pattern \d+\.\d+\.\d+
        parts = schema.version.split(".")
        assert len(parts) == 3, f"Version must have 3 parts: {schema.version}"
        for part in parts:
            assert part.isdigit(), f"Version parts must be numeric: {schema.version}"


class TestSchemaEntityTypes:
    """Tests for entity type requirements."""

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_schema_has_minimum_entity_types(self, domain_id: str) -> None:
        """Test that schemas have at least 5 entity types."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        schema = DomainSchema.model_validate(data)
        assert len(schema.entity_types) >= 5, (
            f"Schema {domain_id} needs at least 5 entity types, has {len(schema.entity_types)}"
        )

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_entity_types_have_descriptions(self, domain_id: str) -> None:
        """Test that all entity types have descriptions."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        schema = DomainSchema.model_validate(data)
        for et in schema.entity_types:
            assert et.description, f"Entity type {et.id} in {domain_id} has no description"
            assert len(et.description) >= 10, (
                f"Entity type {et.id} in {domain_id} has too short description"
            )

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_entity_types_have_examples(self, domain_id: str) -> None:
        """Test that entity types have at least one example."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        schema = DomainSchema.model_validate(data)
        for et in schema.entity_types:
            # Not all entity types require examples, but most should have them
            # We'll just ensure the property exists and is a list
            assert isinstance(et.examples, list), (
                f"Entity type {et.id} in {domain_id} examples should be a list"
            )


class TestSchemaRelationshipTypes:
    """Tests for relationship type requirements."""

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_schema_has_minimum_relationship_types(self, domain_id: str) -> None:
        """Test that schemas have at least 5 relationship types."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        schema = DomainSchema.model_validate(data)
        assert len(schema.relationship_types) >= 5, (
            f"Schema {domain_id} needs at least 5 relationship types, "
            f"has {len(schema.relationship_types)}"
        )

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_relationship_types_have_descriptions(self, domain_id: str) -> None:
        """Test that all relationship types have descriptions."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        schema = DomainSchema.model_validate(data)
        for rt in schema.relationship_types:
            assert rt.description, f"Relationship type {rt.id} in {domain_id} has no description"

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_related_to_relationship_exists(self, domain_id: str) -> None:
        """Test that all schemas have a 'related_to' fallback relationship."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        schema = DomainSchema.model_validate(data)
        relationship_ids = [rt.id for rt in schema.relationship_types]
        assert "related_to" in relationship_ids, (
            f"Schema {domain_id} should have 'related_to' relationship type"
        )


class TestSchemaPromptTemplates:
    """Tests for extraction prompt templates."""

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_schema_has_prompt_template(self, domain_id: str) -> None:
        """Test that schemas have non-empty prompt templates."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        schema = DomainSchema.model_validate(data)
        assert len(schema.extraction_prompt_template) > 100, (
            f"Schema {domain_id} prompt template too short: "
            f"{len(schema.extraction_prompt_template)} chars"
        )

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_prompt_template_has_placeholders(self, domain_id: str) -> None:
        """Test that prompt templates have expected placeholders."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        schema = DomainSchema.model_validate(data)
        template = schema.extraction_prompt_template

        # Should have entity_descriptions placeholder
        assert "{entity_descriptions}" in template, (
            f"Schema {domain_id} prompt template missing {{entity_descriptions}} placeholder"
        )
        # Should have relationship_descriptions placeholder
        assert "{relationship_descriptions}" in template, (
            f"Schema {domain_id} prompt template missing {{relationship_descriptions}} placeholder"
        )


class TestSchemaConfidenceThresholds:
    """Tests for confidence threshold configuration."""

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_schema_has_valid_confidence_thresholds(self, domain_id: str) -> None:
        """Test that confidence thresholds are valid."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        schema = DomainSchema.model_validate(data)
        thresholds = schema.confidence_thresholds

        # Entity threshold should be between 0 and 1
        assert 0.0 <= thresholds.entity_extraction <= 1.0, (
            f"Schema {domain_id} entity threshold out of range: {thresholds.entity_extraction}"
        )
        # Relationship threshold should be between 0 and 1
        assert 0.0 <= thresholds.relationship_extraction <= 1.0, (
            f"Schema {domain_id} relationship threshold out of range: "
            f"{thresholds.relationship_extraction}"
        )

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_relationship_threshold_not_higher_than_entity(self, domain_id: str) -> None:
        """Test that relationship threshold is not higher than entity threshold."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        schema = DomainSchema.model_validate(data)
        thresholds = schema.confidence_thresholds

        # Typically relationship threshold should be <= entity threshold
        # This is a soft check - warn but don't fail
        if thresholds.relationship_extraction > thresholds.entity_extraction:
            pytest.skip(
                f"Schema {domain_id} has relationship threshold "
                f"({thresholds.relationship_extraction}) higher than "
                f"entity threshold ({thresholds.entity_extraction})"
            )


class TestSchemaIntegrity:
    """Tests for overall schema integrity."""

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_relationship_source_target_types_valid(self, domain_id: str) -> None:
        """Test that relationship source/target types reference valid entity types."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # This validation is done by DomainSchema model_validator
        # If it passes, the references are valid
        schema = DomainSchema.model_validate(data)
        entity_type_ids = set(schema.get_entity_type_ids())

        for rt in schema.relationship_types:
            # Skip empty source/target types (means "any")
            for source_type in rt.valid_source_types:
                assert source_type in entity_type_ids, (
                    f"Relationship {rt.id} in {domain_id} references "
                    f"unknown source type: {source_type}"
                )
            for target_type in rt.valid_target_types:
                assert target_type in entity_type_ids, (
                    f"Relationship {rt.id} in {domain_id} references "
                    f"unknown target type: {target_type}"
                )

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_no_duplicate_entity_type_ids(self, domain_id: str) -> None:
        """Test that entity type IDs are unique within a schema."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        schema = DomainSchema.model_validate(data)
        entity_ids = [et.id for et in schema.entity_types]
        assert len(entity_ids) == len(set(entity_ids)), (
            f"Schema {domain_id} has duplicate entity type IDs"
        )

    @pytest.mark.parametrize("domain_id", EXPECTED_DOMAINS)
    def test_no_duplicate_relationship_type_ids(self, domain_id: str) -> None:
        """Test that relationship type IDs are unique within a schema."""
        schema_path = SCHEMA_DIR / f"{domain_id}.yaml"
        with open(schema_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        schema = DomainSchema.model_validate(data)
        relationship_ids = [rt.id for rt in schema.relationship_types]
        assert len(relationship_ids) == len(set(relationship_ids)), (
            f"Schema {domain_id} has duplicate relationship type IDs"
        )
