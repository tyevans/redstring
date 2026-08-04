"""
Domain events for entity consolidation.

This module defines the events emitted during the entity consolidation
lifecycle, including candidate identification, merge execution,
human review decisions, and undo operations.

All events extend TenantDomainEvent to ensure proper multi-tenant
isolation and are registered with eventsource-py for persistence.
"""

from __future__ import annotations

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
# Candidate Identification Events
# =============================================================================


class MergeCandidateIdentified(TenantDomainEvent):
    """
    Emitted when a potential duplicate entity pair is identified.

    This event is emitted after blocking and similarity computation
    identify a candidate pair that may be duplicates.

    Attributes:
        entity_a_id: First entity in the candidate pair
        entity_b_id: Second entity in the candidate pair
        similarity_scores: Detailed breakdown of similarity scores
        combined_confidence: Final combined confidence score (0.0-1.0)
        blocking_keys_matched: Which blocking keys matched this pair
        identified_by: How the candidate was identified (extraction, batch, manual)
    """

    event_type: str = "MergeCandidateIdentified"
    aggregate_type: str = "ConsolidationProcess"

    entity_a_id: UUID = Field(description="First entity in candidate pair")
    entity_b_id: UUID = Field(description="Second entity in candidate pair")
    similarity_scores: dict = Field(
        description="Detailed similarity score breakdown",
        default_factory=dict,
    )
    combined_confidence: float = Field(
        description="Combined confidence score (0.0-1.0)",
        ge=0.0,
        le=1.0,
    )
    blocking_keys_matched: list[str] = Field(
        description="Blocking keys that matched",
        default_factory=list,
    )
    identified_by: str = Field(
        description="How candidate was identified",
        default="extraction",  # extraction, batch, manual
    )


# =============================================================================
# Merge Execution Events
# =============================================================================


class EntitiesMerged(TenantDomainEvent):
    """
    Emitted when entities are merged into a canonical entity.

    This is the primary merge event, recording the consolidation of
    one or more entities into a canonical entity with full provenance.

    Attributes:
        canonical_entity_id: The surviving canonical entity
        merged_entity_ids: Entities that were merged into canonical
        merge_reason: Why the merge occurred
        similarity_scores: Similarity scores at time of merge
        property_merge_details: How properties were merged
        relationship_transfer_count: Number of relationships transferred
        merged_by_user_id: User who approved/triggered (None for auto)
    """

    event_type: str = "EntitiesMerged"
    aggregate_type: str = "ConsolidationProcess"

    canonical_entity_id: UUID = Field(description="Canonical entity ID")
    merged_entity_ids: list[UUID] = Field(
        description="Entity IDs merged into canonical",
        min_length=1,
    )
    merge_reason: str = Field(
        description="Reason for merge",
        # auto_high_confidence, user_approved, batch, manual
    )
    similarity_scores: dict = Field(
        description="Similarity scores at time of merge",
        default_factory=dict,
    )
    property_merge_details: dict = Field(
        description="Details of how properties were merged",
        default_factory=dict,
    )
    relationship_transfer_count: int = Field(
        description="Number of relationships transferred",
        default=0,
        ge=0,
    )
    merged_by_user_id: UUID | None = Field(
        description="User who approved/triggered merge",
        default=None,
    )


class AliasCreated(TenantDomainEvent):
    """
    Emitted when an alias record is created for a merged entity.

    This event is typically emitted as part of the merge process,
    recording the preservation of the original entity name.

    Attributes:
        alias_id: The alias record ID
        canonical_entity_id: The canonical entity this is an alias of
        alias_name: The original name being recorded as alias
        original_entity_id: Original entity ID before merge
        merge_event_id: Reference to the merge event
    """

    event_type: str = "AliasCreated"
    aggregate_type: str = "ConsolidationProcess"

    alias_id: UUID = Field(description="Alias record ID")
    canonical_entity_id: UUID = Field(description="Canonical entity ID")
    alias_name: str = Field(description="Original name being recorded as alias")
    original_entity_id: UUID = Field(description="Original entity ID before merge")
    merge_event_id: UUID | None = Field(
        description="Reference to the merge event",
        default=None,
    )


# =============================================================================
# Human Review Events
# =============================================================================


class MergeQueuedForReview(TenantDomainEvent):
    """
    Emitted when a merge candidate is queued for human review.

    This event is emitted for candidates with medium confidence scores
    that require human judgment before merging.

    Attributes:
        entity_a_id: First entity in candidate pair
        entity_b_id: Second entity in candidate pair
        confidence: Combined confidence score
        review_priority: Priority for review queue ordering
        similarity_scores: Detailed similarity breakdown
        queue_reason: Why human review is needed
    """

    event_type: str = "MergeQueuedForReview"
    aggregate_type: str = "ConsolidationProcess"

    entity_a_id: UUID = Field(description="First entity in candidate pair")
    entity_b_id: UUID = Field(description="Second entity in candidate pair")
    confidence: float = Field(
        description="Combined confidence score",
        ge=0.0,
        le=1.0,
    )
    review_priority: float = Field(
        description="Priority for queue ordering (1.0 = highest)",
        ge=0.0,
        le=1.0,
    )
    similarity_scores: dict = Field(
        description="Detailed similarity scores",
        default_factory=dict,
    )
    queue_reason: str = Field(
        description="Why review is needed",
        default="medium_confidence",  # medium_confidence, conflicting_signals
    )


class MergeReviewDecision(TenantDomainEvent):
    """
    Emitted when a human makes a review decision on a merge candidate.

    This event records the human decision and serves as training signal
    for the learning feedback loop.

    Attributes:
        review_item_id: The review item ID
        entity_a_id: First entity in candidate pair
        entity_b_id: Second entity in candidate pair
        decision: The decision made (approve, reject, defer, mark_different)
        reviewer_user_id: User who made the decision
        reviewer_notes: Optional notes explaining the decision
        review_duration_seconds: How long the review took
        original_confidence: System's original confidence score
    """

    event_type: str = "MergeReviewDecision"
    aggregate_type: str = "ConsolidationProcess"

    review_item_id: UUID = Field(description="Review item ID")
    entity_a_id: UUID = Field(description="First entity in candidate pair")
    entity_b_id: UUID = Field(description="Second entity in candidate pair")
    decision: str = Field(
        description="Review decision",
        # approve, reject, defer, mark_different
    )
    reviewer_user_id: UUID = Field(description="User who reviewed")
    reviewer_notes: str | None = Field(
        description="Optional review notes",
        default=None,
    )
    review_duration_seconds: int | None = Field(
        description="How long the review took",
        default=None,
        ge=0,
    )
    original_confidence: float = Field(
        description="Original system confidence",
        ge=0.0,
        le=1.0,
    )


# =============================================================================
# Undo and Split Events
# =============================================================================


class MergeUndone(TenantDomainEvent):
    """
    Emitted when a previous merge is undone.

    This event records the reversal of a merge operation, restoring
    the original entities while preserving the merge history.

    Attributes:
        original_merge_event_id: Event ID of the merge being undone
        canonical_entity_id: The canonical entity that was created
        restored_entity_ids: New IDs for the restored entities
        original_entity_ids: Original entity IDs before merge
        undo_reason: Why the merge was undone
        undone_by_user_id: User who initiated the undo
    """

    event_type: str = "MergeUndone"
    aggregate_type: str = "ConsolidationProcess"

    original_merge_event_id: UUID = Field(description="Event ID of original merge")
    canonical_entity_id: UUID = Field(description="Canonical entity from merge")
    restored_entity_ids: list[UUID] = Field(
        description="New UUIDs for restored entities",
    )
    original_entity_ids: list[UUID] = Field(
        description="Original entity IDs before merge",
    )
    undo_reason: str = Field(description="Reason for undo")
    undone_by_user_id: UUID = Field(description="User who initiated undo")


class EntitySplit(TenantDomainEvent):
    """
    Emitted when an entity is split into multiple entities.

    This event records the deliberate splitting of an entity that
    was found to combine multiple distinct concepts.

    Attributes:
        original_entity_id: Entity being split
        new_entity_ids: IDs of the newly created entities
        new_entity_names: Names for the new entities
        property_assignments: Which properties went to which entity
        relationship_assignments: Which relationships went to which entity
        split_reason: Why the entity was split
        split_by_user_id: User who initiated the split
    """

    event_type: str = "EntitySplit"
    aggregate_type: str = "ConsolidationProcess"

    original_entity_id: UUID = Field(description="Entity being split")
    new_entity_ids: list[UUID] = Field(
        description="IDs of new entities",
        min_length=2,
    )
    new_entity_names: list[str] = Field(
        description="Names for the new entities",
        min_length=2,
    )
    property_assignments: dict[str, str] = Field(
        description="Property name -> entity ID mapping",
        default_factory=dict,
    )
    relationship_assignments: dict[str, str] = Field(
        description="Relationship ID -> entity ID mapping",
        default_factory=dict,
    )
    split_reason: str = Field(description="Reason for split")
    split_by_user_id: UUID = Field(description="User who initiated split")


# =============================================================================
# Batch Processing Events
# =============================================================================


class BatchConsolidationStarted(TenantDomainEvent):
    """
    Emitted when a batch consolidation job starts.

    Attributes:
        job_id: Unique identifier for this batch job
        entity_count: Total entities to process
        started_by_user_id: User who triggered the batch
        config_snapshot: Configuration at time of start
    """

    event_type: str = "BatchConsolidationStarted"
    aggregate_type: str = "ConsolidationProcess"

    job_id: UUID = Field(description="Batch job ID")
    entity_count: int = Field(description="Total entities to process", ge=0)
    started_by_user_id: UUID = Field(description="User who started batch")
    config_snapshot: dict = Field(
        description="Config at time of start",
        default_factory=dict,
    )


class BatchConsolidationProgress(TenantDomainEvent):
    """
    Emitted periodically during batch consolidation to report progress.

    Attributes:
        job_id: Batch job ID
        entities_processed: Number of entities processed so far
        candidates_found: Number of merge candidates identified
        merges_performed: Number of auto-merges performed so far
        reviews_queued: Number of items queued for review so far
    """

    event_type: str = "BatchConsolidationProgress"
    aggregate_type: str = "ConsolidationProcess"

    job_id: UUID = Field(description="Batch job ID")
    entities_processed: int = Field(description="Entities processed so far", ge=0)
    candidates_found: int = Field(description="Candidates identified", ge=0)
    merges_performed: int = Field(description="Auto-merges so far", ge=0)
    reviews_queued: int = Field(description="Reviews queued so far", ge=0)


class BatchConsolidationCompleted(TenantDomainEvent):
    """
    Emitted when a batch consolidation job completes.

    Attributes:
        job_id: Batch job ID
        entities_processed: Number of entities processed
        candidates_found: Total merge candidates identified
        merges_performed: Number of auto-merges
        reviews_queued: Number of items queued for review
        duration_seconds: Total duration
        errors: Any errors encountered
    """

    event_type: str = "BatchConsolidationCompleted"
    aggregate_type: str = "ConsolidationProcess"

    job_id: UUID = Field(description="Batch job ID")
    entities_processed: int = Field(description="Entities processed", ge=0)
    candidates_found: int = Field(description="Candidates identified", ge=0)
    merges_performed: int = Field(description="Auto-merges performed", ge=0)
    reviews_queued: int = Field(description="Items queued for review", ge=0)
    duration_seconds: int = Field(description="Total duration", ge=0)
    errors: list[str] = Field(description="Errors encountered", default_factory=list)


class BatchConsolidationFailed(TenantDomainEvent):
    """
    Emitted when a batch consolidation job fails.

    Attributes:
        job_id: Batch job ID
        error_message: Error description
        entities_processed: Entities processed before failure
        failed_at_entity_id: Entity ID where failure occurred (if applicable)
    """

    event_type: str = "BatchConsolidationFailed"
    aggregate_type: str = "ConsolidationProcess"

    job_id: UUID = Field(description="Batch job ID")
    error_message: str = Field(description="Error description")
    entities_processed: int = Field(description="Entities processed before failure", ge=0)
    failed_at_entity_id: UUID | None = Field(
        description="Entity where failure occurred",
        default=None,
    )


# =============================================================================
# Configuration Events
# =============================================================================


class ConsolidationConfigUpdated(TenantDomainEvent):
    """
    Emitted when consolidation configuration is updated.

    Attributes:
        updated_fields: Which fields were changed
        old_values: Previous values for changed fields
        new_values: New values for changed fields
        updated_by_user_id: User who made the change
    """

    event_type: str = "ConsolidationConfigUpdated"
    aggregate_type: str = "ConsolidationConfig"

    updated_fields: list[str] = Field(description="Fields that were changed")
    old_values: dict = Field(description="Previous values", default_factory=dict)
    new_values: dict = Field(description="New values", default_factory=dict)
    updated_by_user_id: UUID = Field(description="User who made the change")


# =============================================================================
# Job-based Consolidation Events
# =============================================================================


class ConsolidationCompleted(TenantDomainEvent):
    """
    Emitted when consolidation for a scraping job completes.

    This event is emitted at the end of the consolidation stage
    in the scraping job pipeline.

    Attributes:
        job_id: The scraping job ID
        entities_processed: Number of entities processed
        candidates_found: Number of merge candidates found
        auto_merged: Number of auto-merges performed
        completed_at: When consolidation completed
    """

    event_type: str = "ConsolidationCompleted"
    aggregate_type: str = "ConsolidationProcess"

    job_id: UUID = Field(description="Scraping job ID")
    entities_processed: int = Field(description="Entities processed", ge=0)
    candidates_found: int = Field(description="Candidates found", ge=0)
    auto_merged: int = Field(description="Auto-merges performed", ge=0)
    completed_at: datetime | None = Field(
        description="Completion timestamp",
        default=None,
    )
