"""Unit tests for DomainSchemaRegistry.

Tests the singleton registry pattern, schema loading, accessor methods,
and error handling for the domain schema registry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Import directly from the domains subpackage to avoid triggering
# the full kg_builder.extraction package which pulls in database dependencies
from kg_builder.extraction.domains.models import DomainSchema, DomainSummary
from kg_builder.extraction.domains.registry import (
    DEFAULT_SCHEMA_DIR,
    DomainSchemaRegistry,
    get_default_domain_schema,
    get_domain_registry,
    get_domain_schema,
    get_registry_dependency,
    is_valid_domain,
    list_available_domains,
    reset_registry_cache,
)

# Sample YAML schema content for testing
VALID_SCHEMA_YAML = """
domain_id: test_domain
display_name: Test Domain
description: A test domain for unit tests
entity_types:
  - id: test_entity
    description: A test entity type
relationship_types:
  - id: test_relationship
    description: A test relationship type
extraction_prompt_template: |
  Extract entities from: {content}
"""

VALID_SCHEMA_2_YAML = """
domain_id: second_domain
display_name: Second Domain
description: Another test domain
entity_types:
  - id: entity_two
    description: Second entity type
relationship_types:
  - id: rel_two
    description: Second relationship type
extraction_prompt_template: |
  Extract: {content}
"""

INVALID_YAML = """
domain_id: invalid
display_name: Invalid Domain
# Missing required fields
"""


class TestDomainSchemaRegistrySingleton:
    """Tests for singleton pattern behavior."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        reset_registry_cache()
        yield
        reset_registry_cache()

    def test_get_instance_returns_same_instance(self):
        """Test that get_instance returns the same instance."""
        instance1 = DomainSchemaRegistry.get_instance()
        instance2 = DomainSchemaRegistry.get_instance()
        assert instance1 is instance2

    def test_force_new_creates_new_instance(self, tmp_path: Path):
        """Test that force_new bypasses singleton."""
        instance1 = DomainSchemaRegistry.get_instance()
        instance2 = DomainSchemaRegistry.get_instance(
            schema_dir=tmp_path,
            force_new=True,
        )
        assert instance1 is not instance2

    def test_reset_instance_clears_singleton(self):
        """Test that reset_instance clears the singleton."""
        instance1 = DomainSchemaRegistry.get_instance()
        DomainSchemaRegistry.reset_instance()
        instance2 = DomainSchemaRegistry.get_instance()
        assert instance1 is not instance2

    def test_thread_safety_double_check_locking(self):
        """Test that singleton is thread-safe."""
        import threading

        instances = []
        errors = []

        def get_instance():
            try:
                instance = DomainSchemaRegistry.get_instance()
                instances.append(instance)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # All instances should be the same object
        assert all(inst is instances[0] for inst in instances)


class TestDomainSchemaRegistryLoading:
    """Tests for schema loading functionality."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        reset_registry_cache()
        yield
        reset_registry_cache()

    @pytest.fixture
    def registry_with_empty_dir(self, tmp_path: Path) -> DomainSchemaRegistry:
        """Create a registry with an empty schema directory."""
        return DomainSchemaRegistry.get_instance(
            schema_dir=tmp_path,
            force_new=True,
        )

    @pytest.fixture
    def registry_with_schemas(self, tmp_path: Path) -> DomainSchemaRegistry:
        """Create a registry with test schemas."""
        # Create test schema files
        (tmp_path / "test_domain.yaml").write_text(VALID_SCHEMA_YAML)
        (tmp_path / "second_domain.yaml").write_text(VALID_SCHEMA_2_YAML)

        return DomainSchemaRegistry.get_instance(
            schema_dir=tmp_path,
            force_new=True,
        )

    def test_load_schemas_from_directory(self, registry_with_schemas: DomainSchemaRegistry):
        """Test loading schemas from a directory."""
        count = registry_with_schemas.load_schemas()
        assert count == 2
        assert registry_with_schemas.is_loaded
        assert len(registry_with_schemas) == 2

    def test_load_schemas_returns_count(self, registry_with_schemas: DomainSchemaRegistry):
        """Test that load_schemas returns the count of loaded schemas."""
        count = registry_with_schemas.load_schemas()
        assert count == 2

    def test_load_schemas_skips_reload_when_loaded(
        self, registry_with_schemas: DomainSchemaRegistry
    ):
        """Test that load_schemas skips reload if already loaded."""
        registry_with_schemas.load_schemas()
        # Second call should not reload
        count = registry_with_schemas.load_schemas()
        assert count == 2

    def test_reload_schemas_forces_reload(self, registry_with_schemas: DomainSchemaRegistry):
        """Test that reload_schemas forces a reload."""
        registry_with_schemas.load_schemas()
        count = registry_with_schemas.reload_schemas()
        assert count == 2

    def test_ensure_loaded_triggers_loading(self, registry_with_schemas: DomainSchemaRegistry):
        """Test that ensure_loaded triggers loading if not loaded."""
        assert not registry_with_schemas.is_loaded
        registry_with_schemas.ensure_loaded()
        assert registry_with_schemas.is_loaded

    def test_empty_directory_loads_zero_schemas(
        self, registry_with_empty_dir: DomainSchemaRegistry
    ):
        """Test loading from an empty directory."""
        count = registry_with_empty_dir.load_schemas()
        assert count == 0
        assert len(registry_with_empty_dir) == 0

    def test_missing_directory_raises_error(self):
        """Test that missing directory raises FileNotFoundError."""
        registry = DomainSchemaRegistry.get_instance(
            schema_dir=Path("/nonexistent/path"),
            force_new=True,
        )
        with pytest.raises(FileNotFoundError):
            registry.load_schemas()

    def test_invalid_schema_raises_error(self, tmp_path: Path):
        """Test that invalid schema raises error."""
        from kg_builder.extraction.domains.loader import SchemaLoadError

        (tmp_path / "invalid.yaml").write_text(INVALID_YAML)

        registry = DomainSchemaRegistry.get_instance(
            schema_dir=tmp_path,
            force_new=True,
        )
        with pytest.raises(SchemaLoadError):
            registry.load_schemas()


class TestDomainSchemaRegistryAccessors:
    """Tests for schema accessor methods."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        reset_registry_cache()
        yield
        reset_registry_cache()

    @pytest.fixture
    def registry(self, tmp_path: Path) -> DomainSchemaRegistry:
        """Create a registry with test schemas."""
        (tmp_path / "test_domain.yaml").write_text(VALID_SCHEMA_YAML)
        (tmp_path / "second_domain.yaml").write_text(VALID_SCHEMA_2_YAML)

        reg = DomainSchemaRegistry.get_instance(
            schema_dir=tmp_path,
            force_new=True,
        )
        reg.load_schemas()
        return reg

    def test_get_schema_returns_schema(self, registry: DomainSchemaRegistry):
        """Test getting a loaded schema."""
        schema = registry.get_schema("test_domain")
        assert isinstance(schema, DomainSchema)
        assert schema.domain_id == "test_domain"
        assert schema.display_name == "Test Domain"

    def test_get_schema_case_insensitive(self, registry: DomainSchemaRegistry):
        """Test that domain IDs are case-insensitive."""
        schema1 = registry.get_schema("test_domain")
        schema2 = registry.get_schema("TEST_DOMAIN")
        schema3 = registry.get_schema("Test_Domain")
        assert schema1 is schema2 is schema3

    def test_get_schema_trims_whitespace(self, registry: DomainSchemaRegistry):
        """Test that domain IDs are whitespace-trimmed."""
        schema1 = registry.get_schema("test_domain")
        schema2 = registry.get_schema("  test_domain  ")
        assert schema1 is schema2

    def test_get_schema_raises_for_unknown(self, registry: DomainSchemaRegistry):
        """Test that unknown domain raises KeyError."""
        with pytest.raises(KeyError) as exc_info:
            registry.get_schema("unknown_domain")
        assert "unknown_domain" in str(exc_info.value)
        # Should list available domains
        assert "test_domain" in str(exc_info.value) or "second_domain" in str(exc_info.value)

    def test_get_schema_or_none_returns_schema(self, registry: DomainSchemaRegistry):
        """Test get_schema_or_none for existing domain."""
        schema = registry.get_schema_or_none("test_domain")
        assert schema is not None
        assert schema.domain_id == "test_domain"

    def test_get_schema_or_none_returns_none(self, registry: DomainSchemaRegistry):
        """Test get_schema_or_none for unknown domain."""
        result = registry.get_schema_or_none("unknown_domain")
        assert result is None

    def test_list_domains_returns_summaries(self, registry: DomainSchemaRegistry):
        """Test listing all domains."""
        domains = registry.list_domains()
        assert len(domains) == 2
        assert all(isinstance(d, DomainSummary) for d in domains)

    def test_list_domains_sorted_by_display_name(self, registry: DomainSchemaRegistry):
        """Test that domains are sorted by display_name."""
        domains = registry.list_domains()
        display_names = [d.display_name for d in domains]
        assert display_names == sorted(display_names)

    def test_list_domain_ids_returns_ids(self, registry: DomainSchemaRegistry):
        """Test listing all domain IDs."""
        ids = registry.list_domain_ids()
        assert sorted(ids) == ["second_domain", "test_domain"]

    def test_has_domain_returns_true(self, registry: DomainSchemaRegistry):
        """Test has_domain for existing domain."""
        assert registry.has_domain("test_domain") is True
        assert registry.has_domain("TEST_DOMAIN") is True

    def test_has_domain_returns_false(self, registry: DomainSchemaRegistry):
        """Test has_domain for non-existing domain."""
        assert registry.has_domain("unknown") is False

    def test_contains_operator(self, registry: DomainSchemaRegistry):
        """Test using 'in' operator."""
        assert "test_domain" in registry
        assert "unknown" not in registry

    def test_len_operator(self, registry: DomainSchemaRegistry):
        """Test using len() operator."""
        assert len(registry) == 2

    def test_iter_operator(self, registry: DomainSchemaRegistry):
        """Test iterating over registry."""
        schemas = list(registry)
        assert len(schemas) == 2
        assert all(isinstance(s, DomainSchema) for s in schemas)

    def test_repr(self, registry: DomainSchemaRegistry):
        """Test string representation."""
        repr_str = repr(registry)
        assert "DomainSchemaRegistry" in repr_str
        assert "loaded=True" in repr_str
        assert "count=2" in repr_str


class TestDomainSchemaRegistryLazyLoading:
    """Tests for lazy loading behavior."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        reset_registry_cache()
        yield
        reset_registry_cache()

    @pytest.fixture
    def registry(self, tmp_path: Path) -> DomainSchemaRegistry:
        """Create a registry with test schemas (not loaded)."""
        (tmp_path / "test_domain.yaml").write_text(VALID_SCHEMA_YAML)
        return DomainSchemaRegistry.get_instance(
            schema_dir=tmp_path,
            force_new=True,
        )

    def test_get_schema_triggers_lazy_load(self, registry: DomainSchemaRegistry):
        """Test that get_schema triggers lazy loading."""
        assert not registry.is_loaded
        schema = registry.get_schema("test_domain")
        assert registry.is_loaded
        assert schema.domain_id == "test_domain"

    def test_list_domains_triggers_lazy_load(self, registry: DomainSchemaRegistry):
        """Test that list_domains triggers lazy loading."""
        assert not registry.is_loaded
        domains = registry.list_domains()
        assert registry.is_loaded
        assert len(domains) == 1

    def test_has_domain_triggers_lazy_load(self, registry: DomainSchemaRegistry):
        """Test that has_domain triggers lazy loading."""
        assert not registry.is_loaded
        result = registry.has_domain("test_domain")
        assert registry.is_loaded
        assert result is True

    def test_len_triggers_lazy_load(self, registry: DomainSchemaRegistry):
        """Test that len() triggers lazy loading."""
        assert not registry.is_loaded
        length = len(registry)
        assert registry.is_loaded
        assert length == 1


class TestDomainSchemaRegistryHotReload:
    """Tests for hot reload functionality."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        reset_registry_cache()
        yield
        reset_registry_cache()

    def test_hot_reload_disabled_by_default(self, tmp_path: Path):
        """Test that hot reload is disabled by default."""
        registry = DomainSchemaRegistry.get_instance(
            schema_dir=tmp_path,
            force_new=True,
        )
        assert not registry.hot_reload

    def test_hot_reload_can_be_enabled(self, tmp_path: Path):
        """Test that hot reload can be enabled."""
        registry = DomainSchemaRegistry.get_instance(
            schema_dir=tmp_path,
            force_new=True,
            hot_reload=True,
        )
        assert registry.hot_reload

    def test_hot_reload_reloads_on_each_access(self, tmp_path: Path):
        """Test that hot reload reloads schemas on each access."""
        (tmp_path / "test_domain.yaml").write_text(VALID_SCHEMA_YAML)

        registry = DomainSchemaRegistry.get_instance(
            schema_dir=tmp_path,
            force_new=True,
            hot_reload=True,
        )

        # First access
        schema = registry.get_schema("test_domain")
        assert schema.description == "A test domain for unit tests"

        # Modify schema file
        modified_yaml = VALID_SCHEMA_YAML.replace(
            "A test domain for unit tests",
            "Modified description",
        )
        (tmp_path / "test_domain.yaml").write_text(modified_yaml)

        # Second access should see the change
        schema = registry.get_schema("test_domain")
        assert schema.description == "Modified description"


class TestDomainSchemaRegistryDefaultSchema:
    """Tests for default schema functionality."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        reset_registry_cache()
        yield
        reset_registry_cache()

    def test_get_default_schema_returns_encyclopedia(self, tmp_path: Path):
        """Test that default schema is encyclopedia_wiki."""
        # Create encyclopedia_wiki schema
        encyclopedia_yaml = """
domain_id: encyclopedia_wiki
display_name: Encyclopedia & Wiki
description: General knowledge content
entity_types:
  - id: concept
    description: A concept
relationship_types:
  - id: related_to
    description: Related to
extraction_prompt_template: Extract from {content}
"""
        (tmp_path / "encyclopedia_wiki.yaml").write_text(encyclopedia_yaml)
        (tmp_path / "test_domain.yaml").write_text(VALID_SCHEMA_YAML)

        registry = DomainSchemaRegistry.get_instance(
            schema_dir=tmp_path,
            force_new=True,
        )
        registry.load_schemas()

        default = registry.get_default_schema()
        assert default is not None
        assert default.domain_id == "encyclopedia_wiki"

    def test_get_default_schema_fallback_to_first(self, tmp_path: Path):
        """Test that default falls back to first schema if no encyclopedia."""
        (tmp_path / "test_domain.yaml").write_text(VALID_SCHEMA_YAML)

        registry = DomainSchemaRegistry.get_instance(
            schema_dir=tmp_path,
            force_new=True,
        )
        registry.load_schemas()

        default = registry.get_default_schema()
        assert default is not None
        assert default.domain_id == "test_domain"

    def test_get_default_schema_returns_none_when_empty(self, tmp_path: Path):
        """Test that default returns None when no schemas loaded."""
        registry = DomainSchemaRegistry.get_instance(
            schema_dir=tmp_path,
            force_new=True,
        )
        registry.load_schemas()

        default = registry.get_default_schema()
        assert default is None


class TestDomainSchemaRegistryEntityTypeSearch:
    """Tests for finding schemas by entity type."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        reset_registry_cache()
        yield
        reset_registry_cache()

    @pytest.fixture
    def registry(self, tmp_path: Path) -> DomainSchemaRegistry:
        """Create a registry with test schemas."""
        (tmp_path / "test_domain.yaml").write_text(VALID_SCHEMA_YAML)
        (tmp_path / "second_domain.yaml").write_text(VALID_SCHEMA_2_YAML)

        reg = DomainSchemaRegistry.get_instance(
            schema_dir=tmp_path,
            force_new=True,
        )
        reg.load_schemas()
        return reg

    def test_get_schemas_for_entity_type(self, registry: DomainSchemaRegistry):
        """Test finding schemas that support an entity type."""
        schemas = registry.get_schemas_for_entity_type("test_entity")
        assert len(schemas) == 1
        assert schemas[0].domain_id == "test_domain"

    def test_get_schemas_for_entity_type_not_found(self, registry: DomainSchemaRegistry):
        """Test finding schemas for unknown entity type."""
        schemas = registry.get_schemas_for_entity_type("unknown_type")
        assert len(schemas) == 0


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    @pytest.fixture(autouse=True)
    def setup_registry(self, tmp_path: Path):
        """Setup registry with test schema for each test."""
        reset_registry_cache()

        (tmp_path / "test_domain.yaml").write_text(VALID_SCHEMA_YAML)

        # Configure the singleton with our test directory
        DomainSchemaRegistry._instance = None
        registry = DomainSchemaRegistry.get_instance(schema_dir=tmp_path)
        registry.load_schemas()

        yield

        reset_registry_cache()

    def test_get_domain_registry(self):
        """Test get_domain_registry convenience function."""
        registry = get_domain_registry()
        assert isinstance(registry, DomainSchemaRegistry)
        assert registry.is_loaded

    def test_get_registry_dependency(self):
        """Test get_registry_dependency for FastAPI."""
        registry = get_registry_dependency()
        assert isinstance(registry, DomainSchemaRegistry)
        assert registry.is_loaded

    def test_get_domain_schema_function(self):
        """Test get_domain_schema convenience function."""
        schema = get_domain_schema("test_domain")
        assert schema.domain_id == "test_domain"

    def test_list_available_domains_function(self):
        """Test list_available_domains convenience function."""
        domains = list_available_domains()
        assert len(domains) >= 1
        assert any(d.domain_id == "test_domain" for d in domains)

    def test_is_valid_domain_function(self):
        """Test is_valid_domain convenience function."""
        assert is_valid_domain("test_domain") is True
        assert is_valid_domain("unknown") is False

    def test_get_default_domain_schema_function(self):
        """Test get_default_domain_schema convenience function."""
        default = get_default_domain_schema()
        assert default is not None
        assert isinstance(default, DomainSchema)


class TestRegistryWithRealSchemas:
    """Tests using actual schema directory (if available)."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before and after each test."""
        reset_registry_cache()
        yield
        reset_registry_cache()

    @pytest.mark.skipif(
        not DEFAULT_SCHEMA_DIR.exists(),
        reason="Schema directory does not exist",
    )
    def test_loads_real_schemas(self):
        """Test that real schemas load without errors."""
        registry = DomainSchemaRegistry.get_instance(
            schema_dir=DEFAULT_SCHEMA_DIR,
            force_new=True,
        )
        count = registry.load_schemas()
        assert count >= 1  # At least one schema should exist

    @pytest.mark.skipif(
        not DEFAULT_SCHEMA_DIR.exists(),
        reason="Schema directory does not exist",
    )
    def test_expected_domains_exist(self):
        """Test that expected domains are available."""
        registry = DomainSchemaRegistry.get_instance(
            schema_dir=DEFAULT_SCHEMA_DIR,
            force_new=True,
        )
        registry.load_schemas()

        expected = [
            "technical_documentation",
            "literature_fiction",
            "news_journalism",
            "academic_research",
            "encyclopedia_wiki",
            "business_corporate",
        ]

        for domain_id in expected:
            assert registry.has_domain(domain_id), f"Expected domain {domain_id} not found"
