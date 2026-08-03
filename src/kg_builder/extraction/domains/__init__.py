"""Domain schema models and registry for adaptive extraction.

This package provides infrastructure for domain-specific extraction:
- Domain schemas defining entity types, relationship types, and prompts
- YAML loader for loading schemas from files
- Registry for loading and managing domain schemas (singleton pattern)
- Models for classification results and extraction strategies

Example usage:
    from kg_builder.extraction.domains import (
        DomainSchema,
        EntityTypeSchema,
        RelationshipTypeSchema,
        ClassificationResult,
        ExtractionStrategy,
        load_schema_from_file,
        load_all_schemas,
        DomainSchemaRegistry,
        get_domain_schema,
        list_available_domains,
    )

    # Create a domain schema programmatically
    schema = DomainSchema(
        domain_id="literature_fiction",
        display_name="Literature & Fiction",
        description="Novels, plays, and narrative works",
        entity_types=[
            EntityTypeSchema(id="character", description="A person in the narrative"),
        ],
        relationship_types=[
            RelationshipTypeSchema(id="loves", description="Romantic love between characters"),
        ],
        extraction_prompt_template="Extract entities from: {content}",
    )

    # Or load from YAML files using the loader
    schemas = load_all_schemas()
    literature_schema = schemas["literature_fiction"]

    # Or use the registry (recommended for application code)
    registry = DomainSchemaRegistry.get_instance()
    registry.load_schemas()
    schema = registry.get_schema("literature_fiction")

    # Or use convenience functions
    schema = get_domain_schema("literature_fiction")
    domains = list_available_domains()
"""

from kg_builder.extraction.domains.loader import (
    SchemaLoadError,
    get_available_domain_ids,
    get_schema_directory,
    load_all_schemas,
    load_schema_from_file,
    load_schema_from_string,
    validate_schema_file,
)
from kg_builder.extraction.domains.models import (
    ClassificationResult,
    ConfidenceThresholds,
    DomainSchema,
    DomainSummary,
    EntityTypeSchema,
    ExtractionStrategy,
    PropertySchema,
    RelationshipTypeSchema,
)
from kg_builder.extraction.domains.registry import (
    DomainSchemaRegistry,
    get_default_domain_schema,
    get_domain_registry,
    get_domain_schema,
    get_registry_dependency,
    is_valid_domain,
    list_available_domains,
    reset_registry_cache,
)

__all__ = [
    # Models
    "ClassificationResult",
    "ConfidenceThresholds",
    "DomainSchema",
    "DomainSummary",
    "EntityTypeSchema",
    "ExtractionStrategy",
    "PropertySchema",
    "RelationshipTypeSchema",
    # Loader functions
    "SchemaLoadError",
    "get_available_domain_ids",
    "get_schema_directory",
    "load_all_schemas",
    "load_schema_from_file",
    "load_schema_from_string",
    "validate_schema_file",
    # Registry
    "DomainSchemaRegistry",
    "get_default_domain_schema",
    "get_domain_registry",
    "get_domain_schema",
    "get_registry_dependency",
    "is_valid_domain",
    "list_available_domains",
    "reset_registry_cache",
]
