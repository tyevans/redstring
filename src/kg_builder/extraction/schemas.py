"""
Pydantic schemas for LLM extraction output.

These schemas define the structure of extraction results from
pydantic-ai agents. They enforce validation and provide
clear documentation for expected output.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator, model_validator


def truncate_string(v: str | None, max_length: int = 500) -> str | None:
    """Truncate a string to max_length, adding ellipsis if truncated."""
    if v is not None and len(v) > max_length:
        return v[: max_length - 3] + "..."
    return v


# Entity type literals matching EntityType enum from kg_builder.models.extracted_entity
# Includes both general entity types and documentation-specific types
EntityTypeLiteral = Literal[
    # General entity types
    "person",
    "organization",
    "location",
    "event",
    "product",
    "concept",
    "document",
    "date",
    "custom",
    # Documentation-specific types
    "function",
    "class",
    "module",
    "pattern",
    "example",
    "parameter",
    "return_type",
    "exception",
]


# Relationship type literals for entity connections
RelationshipTypeLiteral = Literal[
    # Code structure relationships
    "uses",
    "implements",
    "extends",
    "inherits_from",
    "contains",
    "part_of",
    # Function/method relationships
    "calls",
    "returns",
    "accepts",
    "raises",
    # Dependency relationships
    "depends_on",
    "imports",
    "requires",
    # Documentation relationships
    "documented_in",
    "example_of",
    "demonstrates",
    # Generic relationships
    "related_to",
    "references",
    "defines",
    "instantiates",
]


class FunctionProperties(BaseModel):
    """Properties specific to FUNCTION entities.

    These properties capture the technical details of a function
    as extracted from documentation or code analysis.
    """

    signature: str | None = Field(
        None,
        description="Full function signature (e.g., 'def func(a: int) -> str')",
    )
    parameters: list[dict] = Field(
        default_factory=list,
        description="List of parameters with name, type, and description. Each dict should have 'name', 'type', and optionally 'description' and 'default' keys.",
    )
    return_type: str | None = Field(
        None,
        description="Return type annotation (e.g., 'str', 'list[int]', 'None')",
    )
    is_async: bool = Field(
        False,
        description="Whether the function is async (uses async/await)",
    )
    is_generator: bool = Field(
        False,
        description="Whether the function is a generator (uses yield)",
    )
    decorators: list[str] = Field(
        default_factory=list,
        description="List of decorator names applied to the function",
    )
    docstring: str | None = Field(
        None,
        description="Function docstring content",
    )


class ClassProperties(BaseModel):
    """Properties specific to CLASS entities.

    These properties capture the structure and characteristics
    of a class as extracted from documentation or code.
    """

    base_classes: list[str] = Field(
        default_factory=list,
        description="List of base class names this class inherits from",
    )
    methods: list[str] = Field(
        default_factory=list,
        description="List of method names defined in this class",
    )
    class_methods: list[str] = Field(
        default_factory=list,
        description="List of class method names (@classmethod)",
    )
    static_methods: list[str] = Field(
        default_factory=list,
        description="List of static method names (@staticmethod)",
    )
    properties: list[str] = Field(
        default_factory=list,
        description="List of property names (@property)",
    )
    attributes: list[str] = Field(
        default_factory=list,
        description="List of class/instance attribute names",
    )
    is_abstract: bool = Field(
        False,
        description="Whether the class is abstract (ABC or has abstract methods)",
    )
    is_dataclass: bool = Field(
        False,
        description="Whether the class is a dataclass",
    )
    is_pydantic_model: bool = Field(
        False,
        description="Whether the class is a Pydantic BaseModel",
    )
    docstring: str | None = Field(
        None,
        description="Class docstring content",
    )


class ModuleProperties(BaseModel):
    """Properties specific to MODULE entities.

    These properties describe Python modules and packages
    as extracted from documentation.
    """

    path: str | None = Field(
        None,
        description="Module import path (e.g., 'kg_builder.config')",
    )
    package: str | None = Field(
        None,
        description="Parent package name if applicable",
    )
    submodules: list[str] = Field(
        default_factory=list,
        description="List of submodule names within this module",
    )
    public_api: list[str] = Field(
        default_factory=list,
        description="List of public exports (__all__ members)",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="External dependencies this module requires",
    )
    docstring: str | None = Field(
        None,
        description="Module-level docstring",
    )


class PatternProperties(BaseModel):
    """Properties specific to PATTERN entities.

    These properties describe design patterns, architectural patterns,
    or coding patterns found in documentation.
    """

    category: str | None = Field(
        None,
        description="Pattern category (e.g., 'creational', 'structural', 'behavioral', 'architectural')",
    )
    problem: str | None = Field(
        None,
        description="Problem the pattern solves",
    )
    solution: str | None = Field(
        None,
        description="How the pattern solves the problem",
    )
    consequences: list[str] = Field(
        default_factory=list,
        description="Trade-offs and consequences of using this pattern",
    )
    related_patterns: list[str] = Field(
        default_factory=list,
        description="Names of related or alternative patterns",
    )
    implementation_notes: str | None = Field(
        None,
        description="Notes on implementing this pattern",
    )


class ExampleProperties(BaseModel):
    """Properties specific to EXAMPLE entities.

    These properties describe code examples and usage demonstrations
    found in documentation.
    """

    code_snippet: str | None = Field(
        None,
        description="The example code snippet",
    )
    language: str = Field(
        "python",
        description="Programming language of the example",
    )
    demonstrates: list[str] = Field(
        default_factory=list,
        description="Concepts or features this example demonstrates",
    )
    prerequisites: list[str] = Field(
        default_factory=list,
        description="Prerequisites or imports needed for this example",
    )
    expected_output: str | None = Field(
        None,
        description="Expected output of running this example",
    )
    is_runnable: bool = Field(
        True,
        description="Whether this example is meant to be runnable as-is",
    )


class ParameterProperties(BaseModel):
    """Properties specific to PARAMETER entities.

    These properties describe function or method parameters.
    """

    type_annotation: str | None = Field(
        None,
        description="Type annotation for the parameter",
    )
    default_value: str | None = Field(
        None,
        description="Default value if any",
    )
    is_required: bool = Field(
        True,
        description="Whether the parameter is required",
    )
    is_keyword_only: bool = Field(
        False,
        description="Whether the parameter is keyword-only (*args separator)",
    )
    is_positional_only: bool = Field(
        False,
        description="Whether the parameter is positional-only (/ separator)",
    )
    is_variadic: bool = Field(
        False,
        description="Whether this is *args or **kwargs",
    )


class ExceptionProperties(BaseModel):
    """Properties specific to EXCEPTION entities.

    These properties describe exception classes.
    """

    base_exception: str | None = Field(
        None,
        description="Base exception class this inherits from",
    )
    raised_by: list[str] = Field(
        default_factory=list,
        description="Functions/methods that raise this exception",
    )
    message_template: str | None = Field(
        None,
        description="Template or example of the exception message",
    )
    is_custom: bool = Field(
        True,
        description="Whether this is a custom exception vs built-in",
    )


class ExtractedEntitySchema(BaseModel):
    """Schema for a single extracted entity.

    This schema is used by pydantic-ai to validate LLM output.
    It supports all documentation-specific entity types with
    type-specific properties.

    Attributes:
        name: Entity name as it appears in the documentation
        entity_type: Type of entity (function, class, module, etc.)
        description: Brief description of the entity
        properties: Type-specific properties (see *Properties classes)
        confidence: Confidence score for this extraction (0.0-1.0)
        source_text: Text snippet where the entity was found
        aliases: Alternative names or references for this entity
    """

    name: str = Field(
        description="Entity name as it appears in the documentation. Should be the canonical/primary name.",
        min_length=1,
        max_length=512,
    )
    entity_type: str = Field(
        description="Type of entity. Use documentation-specific types (function, class, module, pattern, example) for technical docs.",
    )

    @field_validator("entity_type", mode="before")
    @classmethod
    def normalize_entity_type(cls, v: str) -> str:
        """Normalize entity type - map unknown types to 'custom' or closest match."""
        if not isinstance(v, str):
            return "custom"
        v_lower = v.lower().strip()
        # Map common alternatives to valid types
        type_mapping = {
            "character": "person",
            "human": "person",
            "individual": "person",
            "company": "organization",
            "place": "location",
            "city": "location",
            "country": "location",
            "thing": "product",
            "item": "product",
            "idea": "concept",
            "theme": "concept",
            "topic": "concept",
            "method": "function",
            "procedure": "function",
            "type": "class",
            "struct": "class",
        }
        if v_lower in type_mapping:
            return type_mapping[v_lower]
        # Check if it's a valid literal type
        valid_types = {
            "person", "organization", "location", "event", "product",
            "concept", "document", "date", "custom", "function", "class",
            "module", "pattern", "example", "parameter", "return_type", "exception"
        }
        if v_lower in valid_types:
            return v_lower
        # Default to custom for unknown types
        return "custom"

    description: str | None = Field(
        None,
        description="Brief description of the entity (1-2 sentences). Focus on what it is and what it does.",
        max_length=1000,
    )
    properties: dict = Field(
        default_factory=dict,
        description="Type-specific properties. Use the appropriate schema: FunctionProperties for functions, ClassProperties for classes, etc.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in this extraction (0.0-1.0). Use 0.9+ for explicit definitions, 0.7-0.9 for inferred entities, below 0.7 for uncertain.",
    )
    source_text: Annotated[
        str | None,
        BeforeValidator(truncate_string),
    ] = Field(
        None,
        description="Text snippet where entity was found (max 500 chars). Include enough context to verify the extraction. Will be truncated if longer.",
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternative names or references for this entity (e.g., 'dict' for 'dictionary')",
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        """Normalize entity name by stripping whitespace."""
        return v.strip()


class ExtractedRelationshipSchema(BaseModel):
    """Schema for a relationship between entities.

    Relationships are directional: source -> target.
    The relationship type indicates how the source relates to the target.

    Examples:
        - ClassA "extends" ClassB (ClassA inherits from ClassB)
        - function_x "calls" function_y
        - module_a "imports" module_b
        - example_1 "demonstrates" pattern_abc

    Attributes:
        source_name: Name of the source entity
        target_name: Name of the target entity
        relationship_type: Type of relationship (directed from source to target)
        confidence: Confidence in this relationship (0.0-1.0)
        context: Context explaining the relationship
        properties: Additional relationship properties (e.g., multiplicity)
    """

    source_name: str = Field(
        description="Name of source entity. Must match an entity name from the entities list.",
        min_length=1,
    )
    target_name: str = Field(
        description="Name of target entity. Must match an entity name from the entities list.",
        min_length=1,
    )
    relationship_type: str = Field(
        description="Type of relationship. Describes how source relates to target.",
    )

    @field_validator("relationship_type", mode="before")
    @classmethod
    def normalize_relationship_type(cls, v: str) -> str:
        """Normalize relationship type - map unknown types to 'related_to'."""
        if not isinstance(v, str):
            return "related_to"
        v_lower = v.lower().strip().replace(" ", "_").replace("-", "_")
        # Map common alternatives
        type_mapping = {
            "is_a": "extends",
            "isa": "extends",
            "parent": "extends",
            "child_of": "part_of",
            "belongs_to": "part_of",
            "member_of": "part_of",
            "has": "contains",
            "owns": "contains",
            "interacts_with": "related_to",
            "knows": "related_to",
            "meets": "related_to",
            "loves": "related_to",
            "hates": "related_to",
            "friend_of": "related_to",
            "enemy_of": "related_to",
            "speaks_to": "related_to",
            "talks_to": "related_to",
            "married_to": "related_to",
            "sibling_of": "related_to",
            "parent_of": "related_to",
            "child_of": "related_to",
            "associated_with": "related_to",
            "connected_to": "related_to",
            "linked_to": "related_to",
        }
        if v_lower in type_mapping:
            return type_mapping[v_lower]
        valid_types = {
            "uses", "implements", "extends", "inherits_from", "contains",
            "part_of", "calls", "returns", "accepts", "raises", "depends_on",
            "imports", "requires", "documented_in", "example_of", "demonstrates",
            "related_to", "references", "defines", "instantiates"
        }
        if v_lower in valid_types:
            return v_lower
        return "related_to"

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in this relationship (0.0-1.0). Use 0.9+ for explicit relationships, lower for inferred.",
    )
    context: str | None = Field(
        None,
        description="Context explaining the relationship (max 500 chars). Include evidence from the text.",
        max_length=500,
    )
    properties: dict = Field(
        default_factory=dict,
        description="Additional relationship properties (e.g., version constraints, optional flag).",
    )

    @field_validator("source_name", "target_name")
    @classmethod
    def normalize_entity_names(cls, v: str) -> str:
        """Normalize entity names by stripping whitespace."""
        return v.strip()

    @model_validator(mode="after")
    def validate_different_entities(self) -> "ExtractedRelationshipSchema":
        """Ensure source and target are different entities."""
        if self.source_name.lower() == self.target_name.lower():
            raise ValueError("Source and target must be different entities")
        return self


class ExtractionResult(BaseModel):
    """Complete extraction result from LLM.

    Contains all entities and relationships extracted from a page.
    This is the top-level container returned by the extraction agent.

    Attributes:
        entities: List of extracted entities
        relationships: List of discovered relationships between entities
        extraction_notes: Optional notes about the extraction process
    """

    entities: list[ExtractedEntitySchema] = Field(
        default_factory=list,
        description="Extracted entities. Include all significant entities found in the text.",
    )
    relationships: list[ExtractedRelationshipSchema] = Field(
        default_factory=list,
        description="Discovered relationships between entities. Only include relationships between entities in the entities list.",
    )
    extraction_notes: str | None = Field(
        None,
        description="Optional notes about the extraction (e.g., ambiguities, skipped content).",
        max_length=1000,
    )

    @property
    def entity_count(self) -> int:
        """Number of extracted entities."""
        return len(self.entities)

    @property
    def relationship_count(self) -> int:
        """Number of discovered relationships."""
        return len(self.relationships)

    def get_entities_by_type(self, entity_type: EntityTypeLiteral) -> list[ExtractedEntitySchema]:
        """Get all entities of a specific type.

        Args:
            entity_type: The entity type to filter by

        Returns:
            List of entities matching the specified type
        """
        return [e for e in self.entities if e.entity_type == entity_type]

    def get_entity_names(self) -> set[str]:
        """Get set of all entity names.

        Returns:
            Set of entity names (normalized to lowercase)
        """
        return {e.name.lower() for e in self.entities}

    @model_validator(mode="after")
    def validate_relationship_references(self) -> "ExtractionResult":
        """Filter relationships to only those with valid entity references.

        Instead of failing validation, we filter out relationships that
        reference non-existent entities. This makes extraction more robust
        when LLMs return inconsistent entity/relationship data.
        """
        entity_names = self.get_entity_names()

        valid_relationships = []
        for rel in self.relationships:
            if rel.source_name.lower() in entity_names and rel.target_name.lower() in entity_names:
                valid_relationships.append(rel)
            # Silently drop invalid relationships instead of failing

        self.relationships = valid_relationships
        return self


# Type aliases for common property types
PropertySchemaType = (
    FunctionProperties
    | ClassProperties
    | ModuleProperties
    | PatternProperties
    | ExampleProperties
    | ParameterProperties
    | ExceptionProperties
)


def get_property_schema_for_type(entity_type: EntityTypeLiteral) -> type[BaseModel] | None:
    """Get the appropriate property schema class for an entity type.

    Args:
        entity_type: The entity type

    Returns:
        The property schema class or None if no specific schema exists
    """
    type_to_schema: dict[str, type[BaseModel]] = {
        "function": FunctionProperties,
        "class": ClassProperties,
        "module": ModuleProperties,
        "pattern": PatternProperties,
        "example": ExampleProperties,
        "parameter": ParameterProperties,
        "exception": ExceptionProperties,
    }
    return type_to_schema.get(entity_type)
