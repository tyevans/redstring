"""
Domain events for web scraping and entity extraction.

These events are emitted during the scraping lifecycle and can be consumed
by projections to update read models or trigger downstream processing.
"""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from kg_builder.events.base import TenantDomainEvent

# `@register_event` was removed from every class below in slice 5b. The
# registry is keyed by wire name and refuses duplicates, and the live schema
# (`kg_builder.events.merge`) legitimately owns `EntitiesMerged` and
# `MergeUndone`. Registration bought these classes nothing anyway: it exists
# so a stored event can be deserialised into its class, and not one of these
# has ever been emitted. See BACKLOG B33 -- this module dies with
# `services/consolidation/merge_service.py` in slice 7.

# =============================================================================
# Scraping Job Events
# =============================================================================


class ScrapingJobCreated(TenantDomainEvent):
    """Emitted when a new scraping job is created."""

    event_type: str = "ScrapingJobCreated"
    aggregate_type: str = "ScrapingJob"

    job_id: UUID
    name: str
    start_url: str
    created_by: str
    config: dict  # Job configuration snapshot


class ScrapingJobStarted(TenantDomainEvent):
    """Emitted when a scraping job begins execution."""

    event_type: str = "ScrapingJobStarted"
    aggregate_type: str = "ScrapingJob"

    job_id: UUID
    celery_task_id: str
    started_at: datetime


class ScrapingJobProgressUpdated(TenantDomainEvent):
    """Emitted periodically during job execution to report progress."""

    event_type: str = "ScrapingJobProgressUpdated"
    aggregate_type: str = "ScrapingJob"

    job_id: UUID
    pages_crawled: int
    entities_extracted: int
    errors_count: int


class ScrapingJobCompleted(TenantDomainEvent):
    """Emitted when a scraping job finishes successfully."""

    event_type: str = "ScrapingJobCompleted"
    aggregate_type: str = "ScrapingJob"

    job_id: UUID
    total_pages: int
    total_entities: int
    duration_seconds: float
    completed_at: datetime


class ScrapingJobFailed(TenantDomainEvent):
    """Emitted when a scraping job fails."""

    event_type: str = "ScrapingJobFailed"
    aggregate_type: str = "ScrapingJob"

    job_id: UUID
    error_message: str
    error_type: str
    failed_at: datetime


class ScrapingJobCancelled(TenantDomainEvent):
    """Emitted when a scraping job is cancelled by user."""

    event_type: str = "ScrapingJobCancelled"
    aggregate_type: str = "ScrapingJob"

    job_id: UUID
    cancelled_by: str
    cancelled_at: datetime


class ScrapingJobPaused(TenantDomainEvent):
    """Emitted when a scraping job is paused."""

    event_type: str = "ScrapingJobPaused"
    aggregate_type: str = "ScrapingJob"

    job_id: UUID
    paused_at: datetime
    pages_completed: int


class ScrapingJobResumed(TenantDomainEvent):
    """Emitted when a paused scraping job is resumed."""

    event_type: str = "ScrapingJobResumed"
    aggregate_type: str = "ScrapingJob"

    job_id: UUID
    resumed_at: datetime


# =============================================================================
# Page Scraping Events
# =============================================================================


class PageScraped(TenantDomainEvent):
    """Emitted when a page is successfully scraped."""

    event_type: str = "PageScraped"
    aggregate_type: str = "ScrapedPage"

    page_id: UUID
    job_id: UUID
    url: str
    content_hash: str
    http_status: int
    depth: int
    scraped_at: datetime


class PageScrapingFailed(TenantDomainEvent):
    """Emitted when a page fails to be scraped."""

    event_type: str = "PageScrapingFailed"
    aggregate_type: str = "ScrapedPage"

    job_id: UUID
    url: str
    error_message: str
    http_status: int | None
    failed_at: datetime


# =============================================================================
# Entity Extraction Events
# =============================================================================


class EntityExtracted(TenantDomainEvent):
    """Emitted when an entity is extracted from a page.

    This event captures all information about a single extracted entity,
    including its type, properties, and extraction metadata.

    Attributes:
        entity_id: Unique identifier for this entity
        page_id: ID of the page this entity was extracted from
        job_id: ID of the scraping job that processed the page
        entity_type: Type of entity (FUNCTION, CLASS, CONCEPT, etc.)
        name: Entity name as extracted from content
        normalized_name: Normalized name for deduplication matching
        description: Optional description of the entity
        properties: Type-specific properties (signature, methods, etc.)
        extraction_method: How the entity was extracted (llm_ollama, etc.)
        confidence_score: Confidence in the extraction (0.0-1.0)
        source_text: Text snippet where entity was found (max 500 chars)
    """

    event_type: str = "EntityExtracted"
    aggregate_type: str = "ExtractedEntity"

    # Existing fields (maintained for backward compatibility)
    entity_id: UUID
    page_id: UUID
    job_id: UUID
    entity_type: str
    name: str
    extraction_method: str
    confidence_score: float

    # New fields (added for knowledge graph extraction)
    normalized_name: str = ""  # Default empty for backward compatibility
    description: str | None = None
    # Type-specific properties (signature, methods, etc.)
    properties: dict = Field(default_factory=dict)
    source_text: str | None = None  # Text snippet where entity was found


class EntitiesExtractedBatch(TenantDomainEvent):
    """Emitted when multiple entities are extracted from a page (batch)."""

    event_type: str = "EntitiesExtractedBatch"
    aggregate_type: str = "ExtractedEntity"

    page_id: UUID
    job_id: UUID
    entity_count: int
    schema_org_count: int
    llm_extracted_count: int
    extracted_at: datetime


class EntityRelationshipCreated(TenantDomainEvent):
    """Emitted when a relationship between entities is created."""

    event_type: str = "EntityRelationshipCreated"
    aggregate_type: str = "EntityRelationship"

    relationship_id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    relationship_type: str
    confidence_score: float


class ExtractionFailed(TenantDomainEvent):
    """Emitted when entity extraction fails for a page."""

    event_type: str = "ExtractionFailed"
    aggregate_type: str = "ScrapedPage"

    page_id: UUID
    job_id: UUID
    error_message: str
    failed_at: datetime


# =============================================================================
# Neo4j Sync Events
# =============================================================================


class EntitySyncedToNeo4j(TenantDomainEvent):
    """Emitted when an entity is synced to Neo4j."""

    event_type: str = "EntitySyncedToNeo4j"
    aggregate_type: str = "ExtractedEntity"

    entity_id: UUID
    neo4j_node_id: str
    synced_at: datetime


class RelationshipSyncedToNeo4j(TenantDomainEvent):
    """Emitted when a relationship is synced to Neo4j."""

    event_type: str = "RelationshipSyncedToNeo4j"
    aggregate_type: str = "EntityRelationship"

    relationship_id: UUID
    neo4j_relationship_id: str
    synced_at: datetime


class Neo4jSyncFailed(TenantDomainEvent):
    """Emitted when Neo4j sync fails."""

    event_type: str = "Neo4jSyncFailed"
    aggregate_type: str = "ExtractedEntity"

    entity_id: UUID | None
    relationship_id: UUID | None
    error_message: str
    failed_at: datetime
