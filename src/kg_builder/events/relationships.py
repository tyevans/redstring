"""
Domain events for pending relationship lifecycle.

These events track the complete lifecycle of pending relationships from
creation through resolution, failure, or expiration. Pending relationships
are created when extraction discovers relationships between entities that
may not yet exist in the database.

All events extend TenantDomainEvent for multi-tenant isolation.
"""

from datetime import datetime
from uuid import UUID

from eventsource import register_event
from pydantic import Field

from kg_builder.events.base import TenantDomainEvent

# =============================================================================
# Pending Relationship Lifecycle Events
# =============================================================================


@register_event
class PendingRelationshipCreated(TenantDomainEvent):
    """
    Emitted when a pending relationship is created during extraction.

    This event is emitted when extraction discovers a relationship between
    entities, but one or both entities may not yet exist. The relationship
    is queued for resolution once all entities are extracted.

    Attributes:
        pending_relationship_id: Unique identifier for this pending relationship
        source_page_id: Page where the relationship was discovered
        job_id: Scraping job that triggered extraction
        source_entity_name: Name of the source entity
        target_entity_name: Name of the target entity
        relationship_type: Type of relationship (CALLS, EXTENDS, etc.)
        confidence_score: Confidence in the relationship extraction
        context: Optional context where relationship was found
    """

    event_type: str = "PendingRelationshipCreated"
    aggregate_type: str = "PendingRelationship"

    pending_relationship_id: UUID = Field(description="Unique ID for this pending relationship")
    source_page_id: UUID = Field(description="Page where relationship was discovered")
    job_id: UUID = Field(description="Scraping job ID")
    source_entity_name: str = Field(description="Name of source entity")
    target_entity_name: str = Field(description="Name of target entity")
    relationship_type: str = Field(description="Type of relationship")
    confidence_score: float = Field(
        description="Confidence in extraction (0.0-1.0)",
        ge=0.0,
        le=1.0,
        default=1.0,
    )
    context: str | None = Field(description="Context where relationship was found", default=None)
    created_at: datetime = Field(description="When the pending relationship was created")


@register_event
class PendingRelationshipResolutionAttempted(TenantDomainEvent):
    """
    Emitted when a resolution attempt is made for a pending relationship.

    This event records each attempt to resolve a pending relationship,
    including whether each entity was found.

    Attributes:
        pending_relationship_id: ID of the pending relationship
        attempt_number: Which attempt this is (1, 2, 3, etc.)
        source_entity_found: Whether the source entity was found
        target_entity_found: Whether the target entity was found
        source_entity_id: ID of source entity if found
        target_entity_id: ID of target entity if found
        attempted_at: When the attempt was made
    """

    event_type: str = "PendingRelationshipResolutionAttempted"
    aggregate_type: str = "PendingRelationship"

    pending_relationship_id: UUID = Field(description="Pending relationship ID")
    attempt_number: int = Field(description="Attempt number", ge=1)
    source_entity_found: bool = Field(description="Whether source entity was found")
    target_entity_found: bool = Field(description="Whether target entity was found")
    source_entity_id: UUID | None = Field(description="Source entity ID if found", default=None)
    target_entity_id: UUID | None = Field(description="Target entity ID if found", default=None)
    attempted_at: datetime = Field(description="When attempt was made")


@register_event
class PendingRelationshipResolved(TenantDomainEvent):
    """
    Emitted when a pending relationship is successfully resolved.

    This event is emitted when both source and target entities are found
    and an EntityRelationship record will be created by the projection handler.

    The event contains all data needed to create the EntityRelationship,
    ensuring events are the source of truth for relationship creation.

    Attributes:
        pending_relationship_id: ID of the resolved pending relationship
        resolved_relationship_id: ID for the new EntityRelationship
        source_entity_id: ID of the resolved source entity
        target_entity_id: ID of the resolved target entity
        resolution_method: How resolution was achieved (deferred_chunk, resume, etc.)
        resolution_attempts: Number of attempts before resolution
        time_to_resolution_ms: Time from creation to resolution in milliseconds
        resolved_at: When the relationship was resolved
        relationship_type: Type of relationship (for EntityRelationship creation)
        confidence_score: Confidence score (for EntityRelationship creation)
        properties: Relationship properties (for EntityRelationship creation)
        domain_id: Domain ID (for EntityRelationship creation)
        job_id: Job ID (for tracking)
    """

    event_type: str = "PendingRelationshipResolved"
    aggregate_type: str = "PendingRelationship"

    pending_relationship_id: UUID = Field(description="Pending relationship ID")
    resolved_relationship_id: UUID = Field(description="Created EntityRelationship ID")
    source_entity_id: UUID = Field(description="Resolved source entity ID")
    target_entity_id: UUID = Field(description="Resolved target entity ID")
    resolution_method: str = Field(
        description="How resolution was achieved",
        default="deferred_chunk",
    )
    resolution_attempts: int = Field(description="Attempts before resolution", ge=1, default=1)
    time_to_resolution_ms: int = Field(description="Time to resolution in ms", ge=0, default=0)
    resolved_at: datetime = Field(description="When resolution occurred")

    # Fields for EntityRelationship creation (projection uses these)
    relationship_type: str = Field(description="Type of relationship", default="")
    confidence_score: float = Field(
        description="Confidence score for the relationship",
        ge=0.0,
        le=1.0,
        default=1.0,
    )
    properties: dict = Field(description="Relationship properties", default_factory=dict)
    domain_id: str | None = Field(description="Domain ID", default=None)
    job_id: UUID | None = Field(description="Job ID for tracking", default=None)


@register_event
class PendingRelationshipFailed(TenantDomainEvent):
    """
    Emitted when a pending relationship fails to resolve after max attempts.

    This event records permanent failure to resolve a pending relationship,
    typically because one or both entities were never extracted.

    Attributes:
        pending_relationship_id: ID of the failed pending relationship
        source_entity_name: Name of the source entity (for diagnostics)
        target_entity_name: Name of the target entity (for diagnostics)
        reason: Why resolution failed (source_missing, target_missing, both_missing)
        resolution_attempts: Number of attempts made before failure
        failed_at: When the failure was recorded
    """

    event_type: str = "PendingRelationshipFailed"
    aggregate_type: str = "PendingRelationship"

    pending_relationship_id: UUID = Field(description="Pending relationship ID")
    source_entity_name: str = Field(description="Source entity name")
    target_entity_name: str = Field(description="Target entity name")
    reason: str = Field(
        description="Failure reason",
        # source_missing, target_missing, both_missing, max_attempts
    )
    resolution_attempts: int = Field(description="Attempts before failure", ge=0, default=0)
    failed_at: datetime = Field(description="When failure was recorded")


@register_event
class PendingRelationshipExpired(TenantDomainEvent):
    """
    Emitted when a pending relationship expires without resolution.

    This event is emitted when a pending relationship has been in the queue
    too long and is being cleaned up without successful resolution.

    Attributes:
        pending_relationship_id: ID of the expired pending relationship
        source_entity_name: Name of the source entity
        target_entity_name: Name of the target entity
        age_seconds: How long the relationship was pending
        resolution_attempts: Number of resolution attempts made
        expired_at: When expiration occurred
    """

    event_type: str = "PendingRelationshipExpired"
    aggregate_type: str = "PendingRelationship"

    pending_relationship_id: UUID = Field(description="Pending relationship ID")
    source_entity_name: str = Field(description="Source entity name")
    target_entity_name: str = Field(description="Target entity name")
    age_seconds: int = Field(description="How long pending (seconds)", ge=0)
    resolution_attempts: int = Field(description="Attempts made", ge=0, default=0)
    expired_at: datetime = Field(description="When expiration occurred")


__all__ = [
    "PendingRelationshipCreated",
    "PendingRelationshipExpired",
    "PendingRelationshipFailed",
    "PendingRelationshipResolutionAttempted",
    "PendingRelationshipResolved",
]
