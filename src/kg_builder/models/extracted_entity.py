"""
Extracted entity models for knowledge graph construction.

This module defines the ExtractedEntity and EntityRelationship models,
which represent entities extracted from scraped pages and their relationships.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    # pgvector not installed, create a placeholder
    Vector = None

from kg_builder.db import Base

# Embedding dimension for bge-m3 model
EMBEDDING_DIMENSION = 1024

if TYPE_CHECKING:
    from kg_builder.models.entity_alias import EntityAlias
    from kg_builder.models.scraped_page import ScrapedPage
    from kg_builder.models.tenant import Tenant


class EntityType(str, enum.Enum):
    """Known entity types for validation and documentation.

    Note: As of the Adaptive Extraction Strategy feature, the database stores
    entity_type as a String(100) to support dynamic domain-specific types.
    This enum is kept for:
    - Backward compatibility with existing code
    - Validation of known/legacy entity types
    - Type hints and autocomplete in IDEs

    Domain-specific types (e.g., 'character', 'theme', 'plot_point') are stored
    directly as strings and do not require enum membership.
    """

    # General entity types
    PERSON = "person"
    ORGANIZATION = "organization"
    LOCATION = "location"
    EVENT = "event"
    PRODUCT = "product"
    CONCEPT = "concept"
    DOCUMENT = "document"
    DATE = "date"
    CUSTOM = "custom"

    # Documentation-specific types (for technical documentation extraction)
    FUNCTION = "function"  # Callable functions with signatures
    CLASS = "class"  # OOP classes with methods/attributes
    MODULE = "module"  # Python modules and packages
    PATTERN = "pattern"  # Design patterns, architectural patterns
    EXAMPLE = "example"  # Code examples and usage demonstrations
    PARAMETER = "parameter"  # Function/method parameters
    RETURN_TYPE = "return_type"  # Function return types
    EXCEPTION = "exception"  # Exception classes

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """Check if a string is a valid known entity type.

        Args:
            value: Entity type string to check

        Returns:
            True if the value matches a known EntityType
        """
        try:
            cls(value.lower())
            return True
        except ValueError:
            return False

    @classmethod
    def get_or_none(cls, value: str) -> EntityType | None:
        """Get EntityType enum from string, or None if not a known type.

        Args:
            value: Entity type string

        Returns:
            EntityType enum value, or None for domain-specific types
        """
        try:
            return cls(value.lower())
        except ValueError:
            return None


class ExtractionMethod(str, enum.Enum):
    """How the entity was extracted."""

    SCHEMA_ORG = "schema_org"  # From JSON-LD/Schema.org markup
    OPEN_GRAPH = "open_graph"  # From Open Graph meta tags
    LLM_CLAUDE = "llm_claude"  # Via Claude semantic extraction
    LLM_OLLAMA = "llm_ollama"  # Via local Ollama LLM extraction
    LLM_OPENAI = "llm_openai"  # Via OpenAI GPT extraction
    PATTERN = "pattern"  # Via regex/pattern matching
    SPACY = "spacy"  # Via spaCy NER fallback
    HYBRID = "hybrid"  # Combination of methods


class DatePrecision(str, enum.Enum):
    """Precision level of temporal data.

    Used to indicate the granularity of extracted dates.
    For example, "1999" would have YEAR precision while
    "March 15, 1999 at 3:45 PM" would have MINUTE precision.

    Defined here rather than in `kg_builder.schemas.timeline` because the
    ORM layer sits below the schema layer and needs these values;
    `schemas.timeline` re-exports them.
    """

    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    HOUR = "hour"
    MINUTE = "minute"


class UncertaintyMarker(str, enum.Enum):
    """Uncertainty indicator for temporal data.

    Captures how certain we are about the extracted date:
    - EXACT: The date is explicitly stated ("on March 15, 1999")
    - APPROXIMATE: The date is approximate ("around 1999", "in the late 90s")
    - CIRCA: Historical approximation ("circa 1500")
    - BEFORE: Event occurred before a date ("before 1999")
    - AFTER: Event occurred after a date ("after 1999")
    - INFERRED: Date was inferred from context, not explicit
    """

    EXACT = "exact"
    APPROXIMATE = "approximate"
    CIRCA = "circa"
    BEFORE = "before"
    AFTER = "after"
    INFERRED = "inferred"


class ExtractedEntity(Base):
    """
    Represents an entity extracted from a scraped page.

    Entities are the building blocks of the knowledge graph. They can be
    people, organizations, locations, events, etc., extracted via Schema.org
    markup or LLM analysis.

    Attributes:
        id: UUID primary key for security
        tenant_id: Foreign key to tenant (RLS enforced)
        source_page_id: Foreign key to source page
        entity_type: Type of entity (person, org, location, etc.)
        name: Entity name as extracted
        normalized_name: Normalized name for deduplication
        description: Optional description of the entity
        external_ids: External identifiers (wikidata, etc.)
        properties: Entity-specific attributes as JSON
        extraction_method: How the entity was extracted
        confidence_score: Confidence in extraction (0.0-1.0)
        source_text: Text snippet where entity was found
        neo4j_node_id: Neo4j node ID for sync tracking
        synced_to_neo4j: Whether entity is synced to Neo4j
        synced_at: When entity was synced to Neo4j
        created_at: Timestamp of creation (inherited)
        updated_at: Timestamp of last update (inherited)
    """

    __tablename__ = "extracted_entities"

    # Primary key - UUID for security
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        insert_default=uuid.uuid4,
        index=True,
        comment="UUID primary key for security",
    )

    # Tenant isolation (RLS enforced)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant this entity belongs to (RLS enforced)",
    )

    # Source reference
    source_page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scraped_pages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Page this entity was extracted from",
    )

    # Entity type - changed from Enum to String for dynamic domain support
    # Supports both legacy enum values (person, organization, etc.) and
    # domain-specific types (character, theme, plot_point, etc.)
    entity_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Entity type (string, supports legacy enum values and domain-specific types)",
    )

    # Original entity type tracking for LLM extraction
    # Stores the raw type from the LLM before normalization, if different
    original_entity_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Original entity type from LLM before normalization (if different)",
    )

    name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        index=True,
        comment="Entity name as extracted",
    )

    normalized_name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        index=True,
        comment="Normalized name for deduplication",
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Optional description of the entity",
    )

    # External identifiers for linking
    external_ids: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        insert_default=dict,
        comment="External identifiers (wikidata, schema_org_id, etc.)",
    )

    # Entity properties
    properties: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        insert_default=dict,
        comment="Entity-specific attributes as JSON",
    )

    # Extraction metadata
    extraction_method: Mapped[ExtractionMethod] = mapped_column(
        SQLEnum(
            ExtractionMethod,
            name="extraction_method",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        index=True,
        comment="How the entity was extracted",
    )

    confidence_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        insert_default=1.0,
        comment="Confidence in extraction (0.0-1.0)",
    )

    source_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Text snippet where entity was found",
    )

    # Neo4j sync status
    neo4j_node_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Neo4j node element ID for sync tracking",
    )

    synced_to_neo4j: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        insert_default=False,
        index=True,
        comment="Whether entity is synced to Neo4j",
    )

    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When entity was synced to Neo4j",
    )

    # Canonical/alias tracking for entity consolidation
    is_alias_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extracted_entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="If set, this entity is an alias of the canonical entity with this ID",
    )

    is_canonical: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        index=True,
        comment="Whether this is a canonical entity (not an alias)",
    )

    # Temporal columns. Populated for entities that denote events; all are
    # nullable because most entities are not temporal at all. Dates are stored
    # timezone-aware, with `date_precision` recording how much of the stored
    # value was actually known -- "1999" and "1999-03-15T10:30" are both held
    # as a full datetime and disambiguated by precision.
    start_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="Event start (or point) date",
    )
    end_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="Event end date, for events spanning a range",
    )
    date_precision: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        comment="Granularity of start_date/end_date (see DatePrecision)",
    )
    uncertainty_marker: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="How certain the extracted date is (see UncertaintyMarker)",
    )
    original_temporal_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="The temporal expression as written in the source text",
    )
    sequence_position: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Relative ordering for events with no resolvable date",
    )
    publication_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the source document was published, distinct from the event date",
    )

    # Embedding vector for semantic similarity (bge-m3: 1024 dimensions)
    # Note: This column is added by migration and requires pgvector extension
    # The type annotation uses Any because Vector type may not be available
    # if pgvector is not installed
    if Vector is not None:
        embedding: Mapped[list[float] | None] = mapped_column(
            Vector(EMBEDDING_DIMENSION),
            nullable=True,
            comment="bge-m3 embedding vector for semantic similarity (1024 dimensions)",
        )

    # Relationships
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        doc="Tenant this entity belongs to",
    )

    source_page: Mapped[ScrapedPage] = relationship(
        "ScrapedPage",
        back_populates="entities",
        doc="Page this entity was extracted from",
    )

    # Relationships where this entity is the source
    outgoing_relationships: Mapped[list[EntityRelationship]] = relationship(
        "EntityRelationship",
        foreign_keys="EntityRelationship.source_entity_id",
        back_populates="source_entity",
        cascade="all, delete-orphan",
        doc="Relationships where this entity is the source",
    )

    # Relationships where this entity is the target
    incoming_relationships: Mapped[list[EntityRelationship]] = relationship(
        "EntityRelationship",
        foreign_keys="EntityRelationship.target_entity_id",
        back_populates="target_entity",
        cascade="all, delete-orphan",
        doc="Relationships where this entity is the target",
    )

    # Self-referential relationship for canonical entity tracking
    canonical_entity: Mapped[ExtractedEntity | None] = relationship(
        "ExtractedEntity",
        remote_side="ExtractedEntity.id",
        foreign_keys=[is_alias_of],
        back_populates="alias_entities",
        doc="The canonical entity this is an alias of",
    )

    alias_entities: Mapped[list[ExtractedEntity]] = relationship(
        "ExtractedEntity",
        foreign_keys="ExtractedEntity.is_alias_of",
        back_populates="canonical_entity",
        doc="Entities that are aliases of this canonical entity",
    )

    # EntityAlias records tracking merged entity names
    aliases: Mapped[list[EntityAlias]] = relationship(
        "EntityAlias",
        back_populates="canonical_entity",
        cascade="all, delete-orphan",
        doc="Aliases that have been merged into this canonical entity",
    )

    def __init__(self, **kwargs):
        """Initialize entity with default values for optional fields."""
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        if "external_ids" not in kwargs:
            kwargs["external_ids"] = {}
        if "properties" not in kwargs:
            kwargs["properties"] = {}
        # Auto-normalize name if not provided
        if "normalized_name" not in kwargs and "name" in kwargs:
            kwargs["normalized_name"] = self._normalize_name(kwargs["name"])
        super().__init__(**kwargs)

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize entity name for deduplication."""
        import unicodedata

        # Lowercase and strip whitespace
        normalized = name.lower().strip()
        # Remove accents
        normalized = unicodedata.normalize("NFKD", normalized)
        normalized = "".join(c for c in normalized if not unicodedata.combining(c))
        # Collapse multiple spaces
        normalized = " ".join(normalized.split())
        return normalized

    @property
    def entity_type_enum(self) -> EntityType | None:
        """Get entity_type as EntityType enum, or None if not a known type.

        Use this property when you need type-safe access to legacy entity types.
        Returns None for domain-specific types like 'character', 'theme'.
        """
        return EntityType.get_or_none(self.entity_type)

    @property
    def is_known_entity_type(self) -> bool:
        """Check if entity_type is a known/legacy type from the EntityType enum."""
        return EntityType.is_valid(self.entity_type)

    @property
    def is_temporal(self) -> bool:
        """Whether this entity is placed in time at all.

        An event with no resolvable date can still be ordered, so a
        sequence position counts as temporal.
        """
        return self.start_date is not None or self.sequence_position is not None

    @property
    def date_precision_enum(self) -> DatePrecision | None:
        """Get date_precision as a DatePrecision, or None if unset or unknown."""
        if self.date_precision is None:
            return None
        try:
            return DatePrecision(self.date_precision)
        except ValueError:
            return None

    @property
    def uncertainty_marker_enum(self) -> UncertaintyMarker | None:
        """Get uncertainty_marker as an UncertaintyMarker, or None if unset or unknown."""
        if self.uncertainty_marker is None:
            return None
        try:
            return UncertaintyMarker(self.uncertainty_marker)
        except ValueError:
            return None

    @property
    def has_date_range(self) -> bool:
        """Whether the entity spans a range rather than a single point."""
        return self.start_date is not None and self.end_date is not None

    def __repr__(self) -> str:
        """Return string representation of the entity."""
        return f"<ExtractedEntity {self.id} '{self.name}' ({self.entity_type})>"


class EntityRelationship(Base):
    """
    Represents a relationship between two extracted entities.

    Relationships form the edges of the knowledge graph, connecting
    entities with typed, directed relationships.

    Attributes:
        id: UUID primary key for security
        tenant_id: Foreign key to tenant (RLS enforced)
        source_entity_id: Source entity of the relationship
        target_entity_id: Target entity of the relationship
        relationship_type: Type of relationship (e.g., WORKS_FOR, LOCATED_IN)
        properties: Additional relationship properties
        confidence_score: Confidence in the relationship (0.0-1.0)
        neo4j_relationship_id: Neo4j relationship ID for sync tracking
        synced_to_neo4j: Whether relationship is synced to Neo4j
        created_at: Timestamp of creation (inherited)
        updated_at: Timestamp of last update (inherited)
    """

    __tablename__ = "entity_relationships"

    # Primary key - UUID for security
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        insert_default=uuid.uuid4,
        index=True,
        comment="UUID primary key for security",
    )

    # Tenant isolation (RLS enforced)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant this relationship belongs to (RLS enforced)",
    )

    # Relationship endpoints
    source_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extracted_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Source entity of the relationship",
    )

    target_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extracted_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Target entity of the relationship",
    )

    # Relationship metadata
    relationship_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Type of relationship (e.g., WORKS_FOR, LOCATED_IN)",
    )

    properties: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        insert_default=dict,
        comment="Additional relationship properties",
    )

    confidence_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        insert_default=1.0,
        comment="Confidence in the relationship (0.0-1.0)",
    )

    # Neo4j sync status
    neo4j_relationship_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Neo4j relationship element ID for sync tracking",
    )

    synced_to_neo4j: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        insert_default=False,
        index=True,
        comment="Whether relationship is synced to Neo4j",
    )

    # Relationships
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        doc="Tenant this relationship belongs to",
    )

    source_entity: Mapped[ExtractedEntity] = relationship(
        "ExtractedEntity",
        foreign_keys=[source_entity_id],
        back_populates="outgoing_relationships",
        doc="Source entity of the relationship",
    )

    target_entity: Mapped[ExtractedEntity] = relationship(
        "ExtractedEntity",
        foreign_keys=[target_entity_id],
        back_populates="incoming_relationships",
        doc="Target entity of the relationship",
    )

    def __init__(self, **kwargs):
        """Initialize relationship with default values for optional fields."""
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        if "properties" not in kwargs:
            kwargs["properties"] = {}
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        """Return string representation of the relationship."""
        return f"<EntityRelationship {self.id} ({self.relationship_type})>"
