"""Pydantic models for domain schemas and extraction configuration.

These models define the structure of domain schemas used for adaptive extraction.
Domain schemas specify:
- Entity types with their properties
- Relationship types with valid source/target constraints
- Extraction prompt templates
- Confidence thresholds

The models follow a hierarchical structure:
    DomainSchema
    +-- EntityTypeSchema (list)
    |   +-- PropertySchema (list)
    +-- RelationshipTypeSchema (list)
    +-- ConfidenceThresholds
    +-- extraction_prompt_template

Example:
    >>> from kg_builder.extraction.domains.models import (
    ...     DomainSchema, EntityTypeSchema, RelationshipTypeSchema, PropertySchema
    ... )
    >>> schema = DomainSchema(
    ...     domain_id="literature_fiction",
    ...     display_name="Literature & Fiction",
    ...     description="Novels, plays, and narrative works",
    ...     entity_types=[
    ...         EntityTypeSchema(
    ...             id="character",
    ...             description="A person or being in the narrative",
    ...             properties=[PropertySchema(name="role", type="string")],
    ...             examples=["Hamlet", "Lady Macbeth"],
    ...         ),
    ...     ],
    ...     relationship_types=[
    ...         RelationshipTypeSchema(id="loves", description="Romantic love"),
    ...     ],
    ...     extraction_prompt_template="Extract entities from: {content}",
    ... )
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PropertySchema(BaseModel):
    """Schema for an entity property.

    Properties define the structured data associated with entity types.
    For example, a 'character' entity might have 'role' and 'allegiance' properties.

    Attributes:
        name: Property identifier (normalized to lowercase with underscores)
        type: Data type of the property value
        description: Human-readable description
        required: Whether this property is required for the entity type
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Property name (e.g., 'role', 'signature')",
    )
    type: Literal["string", "number", "boolean", "array", "object"] = Field(
        default="string",
        description="Property value type",
    )
    description: str | None = Field(
        default=None,
        max_length=500,
        description="Human-readable description of the property",
    )
    required: bool = Field(
        default=False,
        description="Whether this property is required for the entity type",
    )

    @field_validator("name")
    @classmethod
    def validate_property_name(cls, v: str) -> str:
        """Validate and normalize property name format.

        Property names are converted to lowercase with underscores replacing
        spaces and hyphens. The result must be a valid Python identifier.

        Args:
            v: The input property name

        Returns:
            Normalized property name

        Raises:
            ValueError: If the normalized name is not a valid identifier
        """
        # Normalize to lowercase with underscores
        normalized = v.lower().strip().replace(" ", "_").replace("-", "_")
        # Remove consecutive underscores
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        # Strip leading/trailing underscores
        normalized = normalized.strip("_")
        if not normalized:
            raise ValueError(f"Property name cannot be empty after normalization: '{v}'")
        if not normalized.isidentifier():
            raise ValueError(f"Property name must be a valid identifier: '{v}' -> '{normalized}'")
        return normalized


class EntityTypeSchema(BaseModel):
    """Schema for an entity type in a domain.

    Entity types define the categories of entities that can be extracted
    from content in a specific domain. Each entity type has:
    - A unique identifier within the domain
    - A description used in extraction prompts
    - Optional properties specific to the entity type
    - Optional examples for few-shot prompting

    Attributes:
        id: Entity type identifier (normalized to lowercase with underscores)
        description: Human-readable description for extraction prompts
        properties: Entity-specific properties to extract
        examples: Example entity names for few-shot prompting
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Entity type identifier (e.g., 'character', 'function')",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Human-readable description for extraction prompts",
    )
    properties: list[PropertySchema] = Field(
        default_factory=list,
        description="Entity-specific properties to extract",
    )
    examples: list[str] = Field(
        default_factory=list,
        description="Example entity names for few-shot prompting",
    )

    @field_validator("id")
    @classmethod
    def validate_entity_type_id(cls, v: str) -> str:
        """Validate and normalize entity type ID.

        Entity type IDs are converted to lowercase with underscores replacing
        spaces and hyphens. The result must be a valid Python identifier.

        Args:
            v: The input entity type ID

        Returns:
            Normalized entity type ID

        Raises:
            ValueError: If the normalized ID is not a valid identifier
        """
        normalized = v.lower().strip().replace(" ", "_").replace("-", "_")
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        normalized = normalized.strip("_")
        if not normalized:
            raise ValueError(f"Entity type ID cannot be empty after normalization: '{v}'")
        if not normalized.isidentifier():
            raise ValueError(f"Entity type ID must be a valid identifier: '{v}' -> '{normalized}'")
        return normalized

    @field_validator("examples")
    @classmethod
    def validate_examples_length(cls, v: list[str]) -> list[str]:
        """Validate that examples list is not too long.

        Args:
            v: List of example names

        Returns:
            The validated list

        Raises:
            ValueError: If more than 10 examples are provided
        """
        if len(v) > 10:
            raise ValueError(f"Maximum 10 examples allowed, got {len(v)}")
        return v

    def get_property(self, name: str) -> PropertySchema | None:
        """Get property schema by name.

        Args:
            name: Property name to look up

        Returns:
            PropertySchema if found, None otherwise
        """
        normalized_name = name.lower().strip().replace(" ", "_").replace("-", "_")
        for prop in self.properties:
            if prop.name == normalized_name:
                return prop
        return None


class RelationshipTypeSchema(BaseModel):
    """Schema for a relationship type in a domain.

    Relationship types define the edges that can connect entities
    in the knowledge graph. Relationships are directed (source -> target)
    unless marked as bidirectional.

    Attributes:
        id: Relationship type identifier (normalized to lowercase with underscores)
        description: Human-readable description for extraction prompts
        valid_source_types: Allowed source entity types (empty means any)
        valid_target_types: Allowed target entity types (empty means any)
        bidirectional: Whether the relationship is bidirectional
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Relationship type identifier (e.g., 'loves', 'implements')",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Human-readable description for extraction prompts",
    )
    valid_source_types: list[str] = Field(
        default_factory=list,
        description="Valid source entity types (empty = any)",
    )
    valid_target_types: list[str] = Field(
        default_factory=list,
        description="Valid target entity types (empty = any)",
    )
    bidirectional: bool = Field(
        default=False,
        description="Whether the relationship is bidirectional",
    )

    @field_validator("id")
    @classmethod
    def validate_relationship_type_id(cls, v: str) -> str:
        """Validate and normalize relationship type ID.

        Relationship type IDs are converted to lowercase with underscores replacing
        spaces and hyphens. The result must be a valid Python identifier.

        Args:
            v: The input relationship type ID

        Returns:
            Normalized relationship type ID

        Raises:
            ValueError: If the normalized ID is not a valid identifier
        """
        normalized = v.lower().strip().replace(" ", "_").replace("-", "_")
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        normalized = normalized.strip("_")
        if not normalized:
            raise ValueError(f"Relationship type ID cannot be empty after normalization: '{v}'")
        if not normalized.isidentifier():
            raise ValueError(
                f"Relationship type ID must be a valid identifier: '{v}' -> '{normalized}'"
            )
        return normalized

    @field_validator("valid_source_types", "valid_target_types")
    @classmethod
    def normalize_type_lists(cls, v: list[str]) -> list[str]:
        """Normalize entity type references in source/target type lists.

        Args:
            v: List of entity type IDs

        Returns:
            Normalized list of entity type IDs
        """
        return [t.lower().strip().replace(" ", "_").replace("-", "_") for t in v]

    def is_valid_source(self, entity_type: str) -> bool:
        """Check if an entity type is a valid source for this relationship.

        Args:
            entity_type: Entity type to check

        Returns:
            True if valid (or if no constraints specified), False otherwise
        """
        if not self.valid_source_types:
            return True
        normalized = entity_type.lower().strip()
        return normalized in self.valid_source_types

    def is_valid_target(self, entity_type: str) -> bool:
        """Check if an entity type is a valid target for this relationship.

        Args:
            entity_type: Entity type to check

        Returns:
            True if valid (or if no constraints specified), False otherwise
        """
        if not self.valid_target_types:
            return True
        normalized = entity_type.lower().strip()
        return normalized in self.valid_target_types


class ConfidenceThresholds(BaseModel):
    """Confidence thresholds for extraction filtering.

    Different domains may require different confidence thresholds
    based on the nature of the content. Higher thresholds mean
    stricter filtering of uncertain extractions.

    Attributes:
        entity_extraction: Minimum confidence for entity extraction (default: 0.6)
        relationship_extraction: Minimum confidence for relationship extraction (default: 0.5)
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    entity_extraction: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Minimum confidence for entity extraction",
    )
    relationship_extraction: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum confidence for relationship extraction",
    )


class DomainSchema(BaseModel):
    """Complete schema for a content domain.

    A domain schema defines everything needed to extract entities and
    relationships from a specific type of content (e.g., literature, news,
    technical documentation).

    The schema includes:
    - Entity types specific to the domain
    - Relationship types that connect entities
    - Prompt template for LLM extraction
    - Confidence thresholds for filtering
    - Version for schema evolution

    Attributes:
        domain_id: Unique domain identifier (must match pattern ^[a-z][a-z0-9_]*$)
        display_name: Human-readable domain name
        description: Domain description for classification hints
        entity_types: Supported entity types in this domain
        relationship_types: Supported relationship types in this domain
        extraction_prompt_template: Jinja2-style template for extraction prompts
        confidence_thresholds: Domain-specific confidence thresholds
        version: Schema version (semver format)
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    domain_id: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Unique domain identifier (e.g., 'literature_fiction')",
    )
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Human-readable domain name",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Domain description for classification hints",
    )
    entity_types: list[EntityTypeSchema] = Field(
        ...,
        min_length=1,
        description="Supported entity types in this domain",
    )
    relationship_types: list[RelationshipTypeSchema] = Field(
        ...,
        min_length=1,
        description="Supported relationship types in this domain",
    )
    extraction_prompt_template: str = Field(
        ...,
        min_length=1,
        description="Jinja2-style template for extraction prompts",
    )
    confidence_thresholds: ConfidenceThresholds = Field(
        default_factory=ConfidenceThresholds,
        description="Domain-specific confidence thresholds",
    )
    version: str = Field(
        default="1.0.0",
        pattern=r"^\d+\.\d+\.\d+$",
        description="Schema version (semver format)",
    )

    @model_validator(mode="after")
    def validate_relationship_type_references(self) -> DomainSchema:
        """Validate that relationship type source/target types reference valid entity types.

        Returns:
            The validated DomainSchema

        Raises:
            ValueError: If a relationship references an invalid entity type
        """
        entity_type_ids = {et.id for et in self.entity_types}

        for rt in self.relationship_types:
            for source_type in rt.valid_source_types:
                if source_type and source_type not in entity_type_ids:
                    raise ValueError(
                        f"Relationship '{rt.id}' references unknown source type: '{source_type}'. "
                        f"Valid types: {sorted(entity_type_ids)}"
                    )
            for target_type in rt.valid_target_types:
                if target_type and target_type not in entity_type_ids:
                    raise ValueError(
                        f"Relationship '{rt.id}' references unknown target type: '{target_type}'. "
                        f"Valid types: {sorted(entity_type_ids)}"
                    )
        return self

    def get_entity_type_ids(self) -> list[str]:
        """Get list of entity type IDs.

        Returns:
            List of entity type identifier strings
        """
        return [et.id for et in self.entity_types]

    def get_relationship_type_ids(self) -> list[str]:
        """Get list of relationship type IDs.

        Returns:
            List of relationship type identifier strings
        """
        return [rt.id for rt in self.relationship_types]

    def get_entity_type(self, type_id: str) -> EntityTypeSchema | None:
        """Get entity type schema by ID.

        Args:
            type_id: Entity type identifier to look up

        Returns:
            EntityTypeSchema if found, None otherwise
        """
        normalized = type_id.lower().strip()
        for et in self.entity_types:
            if et.id == normalized:
                return et
        return None

    def get_relationship_type(self, type_id: str) -> RelationshipTypeSchema | None:
        """Get relationship type schema by ID.

        Args:
            type_id: Relationship type identifier to look up

        Returns:
            RelationshipTypeSchema if found, None otherwise
        """
        normalized = type_id.lower().strip()
        for rt in self.relationship_types:
            if rt.id == normalized:
                return rt
        return None

    def is_valid_entity_type(self, type_id: str) -> bool:
        """Check if an entity type ID is valid for this domain.

        The special type 'custom' is always valid to allow for flexibility.

        Args:
            type_id: Entity type identifier to check

        Returns:
            True if the type is valid for this domain, False otherwise
        """
        normalized = type_id.lower().strip()
        return normalized in self.get_entity_type_ids() or normalized == "custom"

    def is_valid_relationship_type(self, type_id: str) -> bool:
        """Check if a relationship type ID is valid for this domain.

        The special type 'related_to' is always valid as a fallback.

        Args:
            type_id: Relationship type identifier to check

        Returns:
            True if the type is valid for this domain, False otherwise
        """
        normalized = type_id.lower().strip()
        return normalized in self.get_relationship_type_ids() or normalized == "related_to"

    def validate_relationship(
        self,
        relationship_type: str,
        source_entity_type: str,
        target_entity_type: str,
    ) -> tuple[bool, str | None]:
        """Validate a relationship against this domain's schema.

        Checks that:
        1. The relationship type is valid
        2. The source entity type is allowed
        3. The target entity type is allowed

        Args:
            relationship_type: Relationship type ID
            source_entity_type: Source entity type ID
            target_entity_type: Target entity type ID

        Returns:
            Tuple of (is_valid, error_message). error_message is None if valid.
        """
        rt = self.get_relationship_type(relationship_type)
        if rt is None and relationship_type.lower() != "related_to":
            return (
                False,
                f"Unknown relationship type: '{relationship_type}'",
            )

        if rt is not None:
            if not rt.is_valid_source(source_entity_type):
                return (
                    False,
                    f"'{source_entity_type}' is not a valid source for '{relationship_type}'",
                )
            if not rt.is_valid_target(target_entity_type):
                return (
                    False,
                    f"'{target_entity_type}' is not a valid target for '{relationship_type}'",
                )

        return (True, None)


class DomainSummary(BaseModel):
    """Summary of a domain for listing endpoints.

    Provides a lightweight view of domain information without
    the full schema details. Useful for API responses that list
    available domains.

    Attributes:
        domain_id: Unique domain identifier
        display_name: Human-readable domain name
        description: Domain description
        entity_type_count: Number of entity types
        relationship_type_count: Number of relationship types
        entity_types: List of entity type IDs
        relationship_types: List of relationship type IDs
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    domain_id: str = Field(..., description="Unique domain identifier")
    display_name: str = Field(..., description="Human-readable domain name")
    description: str = Field(..., description="Domain description")
    entity_type_count: int = Field(..., ge=0, description="Number of entity types")
    relationship_type_count: int = Field(..., ge=0, description="Number of relationship types")
    entity_types: list[str] = Field(..., description="List of entity type IDs")
    relationship_types: list[str] = Field(..., description="List of relationship type IDs")

    @classmethod
    def from_schema(cls, schema: DomainSchema) -> DomainSummary:
        """Create summary from full domain schema.

        Args:
            schema: The full DomainSchema to summarize

        Returns:
            A DomainSummary with essential information
        """
        return cls(
            domain_id=schema.domain_id,
            display_name=schema.display_name,
            description=schema.description,
            entity_type_count=len(schema.entity_types),
            relationship_type_count=len(schema.relationship_types),
            entity_types=schema.get_entity_type_ids(),
            relationship_types=schema.get_relationship_type_ids(),
        )


class ClassificationResult(BaseModel):
    """Result of content classification.

    Returned by the ContentClassifier after analyzing content
    to determine its domain. Includes confidence score and
    optional reasoning from the LLM.

    Attributes:
        domain: Classified domain ID
        confidence: Classification confidence score (0.0-1.0)
        reasoning: LLM's reasoning for the classification
        alternatives: Alternative classifications with lower confidence
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    domain: str = Field(
        ...,
        min_length=1,
        description="Classified domain ID (e.g., 'literature_fiction')",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Classification confidence score",
    )
    reasoning: str | None = Field(
        default=None,
        max_length=1000,
        description="LLM's reasoning for the classification",
    )
    alternatives: list[dict[str, float | str]] | None = Field(
        default=None,
        description="Alternative classifications with lower confidence",
    )

    def is_confident(self, threshold: float = 0.7) -> bool:
        """Check if the classification meets a confidence threshold.

        Args:
            threshold: Minimum confidence required (default: 0.7)

        Returns:
            True if confidence >= threshold
        """
        return self.confidence >= threshold
