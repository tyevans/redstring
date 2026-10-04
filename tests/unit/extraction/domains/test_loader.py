"""Unit tests for YAML domain schema loader utilities.

This module tests:
1. Schema directory path resolution
2. Loading schemas from strings
3. Loading schemas from individual files
4. SchemaLoadError exception properties
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from redstring.extraction.domains.loader import (
    SchemaLoadError,
    get_available_domain_ids,
    get_schema_directory,
    load_schema_from_file,
    load_schema_from_string,
)
from redstring.extraction.domains.models import DomainSchema


class TestGetSchemaDirectory:
    """Tests for get_schema_directory function."""

    def test_returns_path_object(self) -> None:
        """Test that function returns a Path object."""
        result = get_schema_directory()
        assert isinstance(result, Path)

    def test_returns_schemas_directory(self) -> None:
        """Test that function returns the schemas directory."""
        result = get_schema_directory()
        assert result.name == "schemas"
        assert "extraction" in str(result)
        assert "domains" in str(result)


class TestLoadSchemaFromString:
    """Tests for load_schema_from_string function."""

    def test_loads_valid_schema(self) -> None:
        """Test loading a valid schema from string."""
        yaml_content = """
domain_id: test_domain
display_name: Test Domain
description: A test domain for unit tests
entity_types:
  - id: entity_one
    description: First entity type
relationship_types:
  - id: relates_to
    description: Generic relationship
extraction_prompt_template: Extract entities from {content}
version: "1.0.0"
"""
        schema = load_schema_from_string(yaml_content)
        assert isinstance(schema, DomainSchema)
        assert schema.domain_id == "test_domain"
        assert schema.display_name == "Test Domain"

    def test_raises_on_invalid_yaml(self) -> None:
        """Test that invalid YAML raises SchemaLoadError."""
        # Use truly invalid YAML (unclosed quotes)
        invalid_yaml = """
domain_id: "unclosed string
display_name: Test
"""
        with pytest.raises(SchemaLoadError) as exc_info:
            load_schema_from_string(invalid_yaml)
        assert "Invalid YAML syntax" in str(exc_info.value)

    def test_raises_on_empty_content(self) -> None:
        """Test that empty content raises SchemaLoadError."""
        with pytest.raises(SchemaLoadError) as exc_info:
            load_schema_from_string("")
        assert "Empty YAML content" in str(exc_info.value)

    def test_raises_on_non_mapping_content(self) -> None:
        """Test that non-mapping YAML raises SchemaLoadError."""
        with pytest.raises(SchemaLoadError) as exc_info:
            load_schema_from_string("- item1\n- item2")
        assert "must be a mapping" in str(exc_info.value)

    def test_raises_on_validation_failure(self) -> None:
        """Test that schema validation failure raises SchemaLoadError."""
        # Missing required fields
        yaml_content = """
domain_id: test_domain
display_name: Test
"""
        with pytest.raises(SchemaLoadError) as exc_info:
            load_schema_from_string(yaml_content)
        assert "validation failed" in str(exc_info.value)

    def test_source_name_in_error_message(self) -> None:
        """Test that source name appears in error messages."""
        with pytest.raises(SchemaLoadError) as exc_info:
            load_schema_from_string("", source_name="my_test_source")
        assert "my_test_source" in str(exc_info.value)


class TestLoadSchemaFromFile:
    """Tests for load_schema_from_file function."""

    def test_loads_valid_schema_file(self) -> None:
        """Test loading a valid schema from file."""
        yaml_content = """
domain_id: file_test
display_name: File Test Domain
description: A test domain loaded from file
entity_types:
  - id: test_entity
    description: A test entity
relationship_types:
  - id: test_rel
    description: A test relationship
extraction_prompt_template: Extract from {content}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = Path(f.name)

        try:
            schema = load_schema_from_file(temp_path)
            assert isinstance(schema, DomainSchema)
            assert schema.domain_id == "file_test"
        finally:
            temp_path.unlink()

    def test_raises_on_file_not_found(self) -> None:
        """Test that missing file raises SchemaLoadError."""
        with pytest.raises(SchemaLoadError) as exc_info:
            load_schema_from_file("/nonexistent/path/schema.yaml")
        assert "not found" in str(exc_info.value)

    def test_raises_on_directory_path(self) -> None:
        """Test that directory path raises SchemaLoadError."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with pytest.raises(SchemaLoadError) as exc_info:
                load_schema_from_file(temp_dir)
            assert "not a file" in str(exc_info.value)

    def test_resolves_relative_path(self) -> None:
        """Test that relative paths are resolved against schema_dir."""
        yaml_content = """
domain_id: relative_test
display_name: Relative Test
description: Testing relative path resolution
entity_types:
  - id: entity
    description: An entity
relationship_types:
  - id: rel
    description: A relationship
extraction_prompt_template: Extract {content}
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "test_schema.yaml"
            temp_path.write_text(yaml_content)

            # Load with relative path and explicit schema_dir
            schema = load_schema_from_file("test_schema.yaml", schema_dir=Path(temp_dir))
            assert schema.domain_id == "relative_test"


class TestGetAvailableDomainIds:
    """Tests for get_available_domain_ids function."""

    def test_returns_list_of_domain_ids(self) -> None:
        """Test that function returns a list of domain IDs."""
        domain_ids = get_available_domain_ids()
        assert isinstance(domain_ids, list)
        assert len(domain_ids) >= 6

    def test_returns_sorted_list(self) -> None:
        """Test that domain IDs are sorted."""
        domain_ids = get_available_domain_ids()
        assert domain_ids == sorted(domain_ids)

    def test_includes_expected_domains(self) -> None:
        """Test that expected domain IDs are included."""
        domain_ids = get_available_domain_ids()
        expected = [
            "academic_research",
            "business_corporate",
            "encyclopedia_wiki",
            "literature_fiction",
            "news_journalism",
            "technical_documentation",
        ]
        for domain_id in expected:
            assert domain_id in domain_ids


class TestSchemaLoadError:
    """Tests for SchemaLoadError exception."""

    def test_has_message(self) -> None:
        """Test that exception has message."""
        error = SchemaLoadError("Test error")
        assert str(error) == "Test error"

    def test_has_file_path(self) -> None:
        """Test that exception stores file path."""
        error = SchemaLoadError("Test error", file_path="/path/to/file.yaml")
        assert error.file_path == Path("/path/to/file.yaml")

    def test_has_cause(self) -> None:
        """Test that exception stores cause."""
        cause = ValueError("Original error")
        error = SchemaLoadError("Test error", cause=cause)
        assert error.cause is cause

    def test_file_path_accepts_string(self) -> None:
        """Test that file_path accepts string."""
        error = SchemaLoadError("Test error", file_path="/path/to/file.yaml")
        assert isinstance(error.file_path, Path)

    def test_file_path_accepts_path(self) -> None:
        """Test that file_path accepts Path."""
        error = SchemaLoadError("Test error", file_path=Path("/path/to/file.yaml"))
        assert isinstance(error.file_path, Path)
