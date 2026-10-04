"""Unit tests for YAML domain schema directory and bulk file validation utilities.

This module tests:
1. Loading all schemas from directories (built-in and custom)
2. Handling error policies and duplicate IDs during bulk loading
3. Validating single schema files against domain models
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from redstring.extraction.domains.loader import (
    SchemaLoadError,
    load_all_schemas,
    validate_schema_file,
)


class TestLoadAllSchemas:
    """Tests for load_all_schemas function."""

    def test_loads_all_built_in_schemas(self) -> None:
        """Test loading all built-in schema files."""
        schemas = load_all_schemas()
        assert isinstance(schemas, dict)
        # Should have at least 6 schemas
        assert len(schemas) >= 6

    def test_returns_dict_keyed_by_domain_id(self) -> None:
        """Test that returned dict is keyed by domain_id."""
        schemas = load_all_schemas()
        for domain_id, schema in schemas.items():
            assert schema.domain_id == domain_id

    def test_loads_from_custom_directory(self) -> None:
        """Test loading schemas from a custom directory."""
        yaml1 = """
domain_id: custom_one
display_name: Custom One
description: First custom schema
entity_types:
  - id: entity
    description: An entity
relationship_types:
  - id: rel
    description: A relationship
extraction_prompt_template: Extract {content}
"""
        yaml2 = """
domain_id: custom_two
display_name: Custom Two
description: Second custom schema
entity_types:
  - id: entity
    description: An entity
relationship_types:
  - id: rel
    description: A relationship
extraction_prompt_template: Extract {content}
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "one.yaml").write_text(yaml1)
            (temp_path / "two.yaml").write_text(yaml2)

            schemas = load_all_schemas(temp_path)
            assert len(schemas) == 2
            assert "custom_one" in schemas
            assert "custom_two" in schemas

    def test_handles_nonexistent_directory(self) -> None:
        """Test that nonexistent directory returns empty dict."""
        schemas = load_all_schemas(Path("/nonexistent/directory"))
        assert schemas == {}

    def test_handles_invalid_schema_with_ignore_errors(self) -> None:
        """Test ignoring errors when loading schemas."""
        valid_yaml = """
domain_id: valid_schema
display_name: Valid Schema
description: A valid schema
entity_types:
  - id: entity
    description: An entity
relationship_types:
  - id: rel
    description: A relationship
extraction_prompt_template: Extract {content}
"""
        invalid_yaml = "not: valid: schema:"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "valid.yaml").write_text(valid_yaml)
            (temp_path / "invalid.yaml").write_text(invalid_yaml)

            # With ignore_errors=True, should skip invalid and load valid
            schemas = load_all_schemas(temp_path, ignore_errors=True)
            assert len(schemas) == 1
            assert "valid_schema" in schemas

    def test_raises_on_invalid_schema_without_ignore_errors(self) -> None:
        """Test raising error for invalid schema when ignore_errors=False."""
        invalid_yaml = "not: valid: schema:"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "invalid.yaml").write_text(invalid_yaml)

            with pytest.raises(SchemaLoadError):
                load_all_schemas(temp_path, ignore_errors=False)

    def test_handles_duplicate_domain_ids_with_ignore_errors(self) -> None:
        """Test handling duplicate domain_ids when ignore_errors=True."""
        yaml_content = """
domain_id: duplicate_id
display_name: Duplicate
description: Schema with duplicate ID
entity_types:
  - id: entity
    description: An entity
relationship_types:
  - id: rel
    description: A relationship
extraction_prompt_template: Extract {content}
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "first.yaml").write_text(yaml_content)
            (temp_path / "second.yaml").write_text(yaml_content)

            # With ignore_errors=True, should load first and skip second
            schemas = load_all_schemas(temp_path, ignore_errors=True)
            assert len(schemas) == 1
            assert "duplicate_id" in schemas


class TestValidateSchemaFile:
    """Tests for validate_schema_file function."""

    def test_returns_true_for_valid_schema(self) -> None:
        """Test that valid schema returns (True, None)."""
        yaml_content = """
domain_id: valid_test
display_name: Valid Test
description: A valid test schema
entity_types:
  - id: entity
    description: An entity
relationship_types:
  - id: rel
    description: A relationship
extraction_prompt_template: Extract {content}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = Path(f.name)

        try:
            is_valid, error = validate_schema_file(temp_path)
            assert is_valid is True
            assert error is None
        finally:
            temp_path.unlink()

    def test_returns_false_for_invalid_schema(self) -> None:
        """Test that invalid schema returns (False, error_message)."""
        invalid_yaml = "domain_id: test"  # Missing required fields

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(invalid_yaml)
            temp_path = Path(f.name)

        try:
            is_valid, error = validate_schema_file(temp_path)
            assert is_valid is False
            assert error is not None
            assert isinstance(error, str)
        finally:
            temp_path.unlink()

    def test_returns_false_for_missing_file(self) -> None:
        """Test that missing file returns (False, error_message)."""
        is_valid, error = validate_schema_file("/nonexistent/file.yaml")
        assert is_valid is False
        assert error is not None
        assert "not found" in error
