"""
Consolidation API request/response schemas.

This module defines Pydantic models for the entity consolidation API,
including merge candidates, merge operations, review queue, and
configuration endpoints.

These schemas build upon the existing similarity schemas in
app/schemas/similarity.py and the domain models in app/models/.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Enums
# =============================================================================


class MergeDecision(str, Enum):
    """Possible decisions for merge candidates."""

    AUTO_MERGE = "auto_merge"  # High confidence - merge automatically
    REVIEW = "review"  # Medium confidence - queue for human review
    REJECT = "reject"  # Low confidence - not duplicates


class ReviewDecision(str, Enum):
    """Human reviewer decisions for merge candidates."""

    APPROVE = "approve"  # Approve the merge
    REJECT = "reject"  # Reject - entities are not duplicates
    DEFER = "defer"  # Defer decision to later


class ReviewStatus(str, Enum):
    """Status values for merge review items."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    EXPIRED = "expired"


class MergeEventType(str, Enum):
    """Types of merge-related events in history."""

    ENTITIES_MERGED = "entities_merged"
    MERGE_UNDONE = "merge_undone"
    ENTITY_SPLIT = "entity_split"


# =============================================================================
# Entity Summary Schemas (for embedding in responses)
# =============================================================================


class EntitySummary(BaseModel):
    """Summary information about an entity for display purposes."""

    id: UUID = Field(description="Entity UUID")
    name: str = Field(description="Entity name")
    normalized_name: Optional[str] = Field(None, description="Normalized entity name")
    entity_type: str = Field(description="Entity type (e.g., 'person', 'organization')")
    description: Optional[str] = Field(None, description="Entity description")
    is_canonical: bool = Field(True, description="Whether this is a canonical entity")

    class Config:
        from_attributes = True


# =============================================================================
# Merge Candidate Schemas
# =============================================================================


class SimilarityBreakdown(BaseModel):
    """Breakdown of similarity scores for transparency."""

    jaro_winkler: Optional[float] = Field(None, ge=0.0, le=1.0)
    levenshtein: Optional[float] = Field(None, ge=0.0, le=1.0)
    trigram: Optional[float] = Field(None, ge=0.0, le=1.0)
    soundex_match: Optional[bool] = None
    metaphone_match: Optional[bool] = None
    embedding_cosine: Optional[float] = Field(None, ge=0.0, le=1.0)
    graph_neighborhood: Optional[float] = Field(None, ge=0.0, le=1.0)
    type_match: Optional[bool] = None
    same_page: Optional[bool] = None


class MergeCandidateResponse(BaseModel):
    """Response model for a single merge candidate pair."""

    entity_a: EntitySummary = Field(description="First entity in the candidate pair")
    entity_b: EntitySummary = Field(description="Second entity in the candidate pair")
    combined_score: float = Field(
        description="Combined similarity score (0.0-1.0)",
        ge=0.0,
        le=1.0,
    )
    confidence: float = Field(
        description="Confidence in the similarity (0.0-1.0)",
        ge=0.0,
        le=1.0,
    )
    decision: MergeDecision = Field(description="Recommended decision")
    similarity_breakdown: SimilarityBreakdown = Field(
        description="Detailed similarity scores"
    )
    blocking_keys: list[str] = Field(
        default_factory=list, description="Blocking keys that matched this pair"
    )
    review_item_id: Optional[UUID] = Field(
        None, description="ID of associated review queue item if queued"
    )
    computed_at: datetime = Field(description="When similarity was computed")


class MergeCandidateListResponse(BaseModel):
    """Paginated list of merge candidates."""

    items: list[MergeCandidateResponse] = Field(
        default_factory=list, description="List of merge candidates"
    )
    total: int = Field(description="Total number of candidates")
    page: int = Field(description="Current page number (1-indexed)")
    page_size: int = Field(description="Items per page")
    pages: int = Field(description="Total number of pages")
    has_next: bool = Field(description="Whether there are more pages")
    has_prev: bool = Field(description="Whether there are previous pages")


class ComputeCandidatesRequest(BaseModel):
    """Request to compute merge candidates."""

    entity_ids: Optional[list[UUID]] = Field(
        None, description="Specific entity IDs to compute candidates for (all if not specified)"
    )
    min_confidence: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="Minimum confidence threshold",
    )
    include_embedding: bool = Field(
        True, description="Include embedding similarity in computation"
    )
    include_graph: bool = Field(
        True, description="Include graph similarity in computation"
    )
    max_candidates_per_entity: int = Field(
        10, ge=1, le=100, description="Maximum candidates to return per entity"
    )


class ComputeCandidatesResponse(BaseModel):
    """Response from candidate computation."""

    job_id: UUID = Field(description="Background job ID for tracking")
    status: str = Field(description="Job status (queued, processing, completed, failed)")
    entities_processed: int = Field(0, description="Number of entities processed")
    candidates_found: int = Field(0, description="Number of candidate pairs found")
    message: Optional[str] = Field(None, description="Status message")


# =============================================================================
# Merge Operation Schemas
# =============================================================================


class MergeRequest(BaseModel):
    """Request to execute a merge operation."""

    canonical_entity_id: UUID = Field(description="ID of the entity to keep (canonical)")
    merged_entity_ids: list[UUID] = Field(
        description="IDs of entities to merge into the canonical",
        min_length=1,
    )
    merge_reason: str = Field(
        "manual",
        description="Reason for merge (manual, auto_high_confidence, user_approved, batch)",
    )
    similarity_scores: Optional[dict] = Field(
        None, description="Similarity scores from candidate computation"
    )

    @field_validator("merged_entity_ids")
    @classmethod
    def validate_merged_entities(cls, v: list[UUID]) -> list[UUID]:
        """Ensure at least one entity is being merged."""
        if not v:
            raise ValueError("At least one entity must be specified to merge")
        return v


class MergeResponse(BaseModel):
    """Response from a merge operation."""

    success: bool = Field(description="Whether merge was successful")
    canonical_entity_id: UUID = Field(description="ID of the surviving canonical entity")
    merged_entity_ids: list[UUID] = Field(description="IDs of entities that were merged")
    aliases_created: int = Field(description="Number of alias records created")
    relationships_transferred: int = Field(description="Number of relationships transferred")
    merge_history_id: UUID = Field(description="ID of the merge history record")
    event_id: UUID = Field(description="Domain event ID for the merge")
    message: Optional[str] = Field(None, description="Additional information")


class UndoMergeRequest(BaseModel):
    """Request to undo a previous merge operation."""

    reason: str = Field(
        description="Reason for undoing the merge",
        min_length=5,
        max_length=500,
    )
    restore_entity_ids: Optional[list[UUID]] = Field(
        None, description="Specific entity IDs to restore (all if not specified)"
    )


class UndoMergeResponse(BaseModel):
    """Response from an undo merge operation."""

    success: bool = Field(description="Whether undo was successful")
    original_merge_event_id: UUID = Field(description="Event ID of the original merge")
    restored_entity_ids: list[UUID] = Field(description="IDs of restored entities")
    aliases_removed: int = Field(description="Number of alias records removed")
    relationships_restored: int = Field(description="Number of relationships restored")
    undo_history_id: UUID = Field(description="ID of the undo history record")
    message: Optional[str] = Field(None, description="Additional information")


class SplitEntityRequest(BaseModel):
    """Request to split an entity into multiple new entities."""

    split_definitions: list[dict] = Field(
        description="Definitions for new entities to create",
        min_length=2,
    )
    relationship_assignments: Optional[dict[str, int]] = Field(
        None, description="Mapping of relationship IDs to new entity index"
    )
    alias_assignments: Optional[dict[str, int]] = Field(
        None, description="Mapping of alias IDs to new entity index"
    )
    reason: str = Field(
        description="Reason for splitting the entity",
        min_length=5,
        max_length=500,
    )

    @field_validator("split_definitions")
    @classmethod
    def validate_split_definitions(cls, v: list[dict]) -> list[dict]:
        """Ensure split definitions have required fields."""
        if len(v) < 2:
            raise ValueError("At least two new entities must be defined for a split")
        for i, defn in enumerate(v):
            if "name" not in defn or not defn["name"]:
                raise ValueError(f"Split definition {i} missing required 'name' field")
        return v


class SplitEntityResponse(BaseModel):
    """Response from an entity split operation."""

    success: bool = Field(description="Whether split was successful")
    original_entity_id: UUID = Field(description="ID of the original entity")
    new_entity_ids: list[UUID] = Field(description="IDs of newly created entities")
    relationships_redistributed: int = Field(description="Number of relationships redistributed")
    aliases_redistributed: int = Field(description="Number of aliases redistributed")
    split_history_id: UUID = Field(description="ID of the split history record")
    message: Optional[str] = Field(None, description="Additional information")


# =============================================================================
# Review Queue Schemas
# =============================================================================


class ReviewQueueItemResponse(BaseModel):
    """Response model for a review queue item."""

    id: UUID = Field(description="Review queue item ID")
    entity_a: EntitySummary = Field(description="First entity in the candidate pair")
    entity_b: EntitySummary = Field(description="Second entity in the candidate pair")
    confidence: float = Field(ge=0.0, le=1.0, description="Similarity confidence")
    review_priority: float = Field(
        ge=0.0, description="Priority in review queue (higher = more urgent)"
    )
    similarity_scores: dict = Field(description="Detailed similarity breakdown")
    status: ReviewStatus = Field(description="Current review status")
    reviewed_by_name: Optional[str] = Field(None, description="Name of reviewer")
    reviewed_at: Optional[datetime] = Field(None, description="When review occurred")
    reviewer_notes: Optional[str] = Field(None, description="Notes from reviewer")
    created_at: datetime = Field(description="When item was queued")

    class Config:
        from_attributes = True


class ReviewQueueListResponse(BaseModel):
    """Paginated list of review queue items."""

    items: list[ReviewQueueItemResponse] = Field(
        default_factory=list, description="List of review queue items"
    )
    total: int = Field(description="Total number of items")
    page: int = Field(description="Current page number (1-indexed)")
    page_size: int = Field(description="Items per page")
    pages: int = Field(description="Total number of pages")
    has_next: bool = Field(description="Whether there are more pages")
    has_prev: bool = Field(description="Whether there are previous pages")


class ReviewDecisionRequest(BaseModel):
    """Request to submit a review decision."""

    decision: ReviewDecision = Field(description="The review decision")
    notes: Optional[str] = Field(
        None,
        description="Optional notes explaining the decision",
        max_length=1000,
    )
    select_canonical: Optional[UUID] = Field(
        None,
        description="If approving, optionally specify which entity should be canonical",
    )


class ReviewDecisionResponse(BaseModel):
    """Response from submitting a review decision."""

    success: bool = Field(description="Whether decision was recorded")
    review_item_id: UUID = Field(description="ID of the review item")
    decision: ReviewDecision = Field(description="The decision that was made")
    merge_executed: bool = Field(
        False, description="Whether merge was immediately executed (for approvals)"
    )
    merge_result: Optional[MergeResponse] = Field(
        None, description="Merge result if merge was executed"
    )
    message: Optional[str] = Field(None, description="Additional information")


class ReviewQueueStatsResponse(BaseModel):
    """Statistics about the review queue."""

    total_pending: int = Field(description="Total items pending review")
    total_approved: int = Field(description="Total items approved")
    total_rejected: int = Field(description="Total items rejected")
    total_deferred: int = Field(description="Total items deferred")
    total_expired: int = Field(description="Total items expired")
    avg_confidence: float = Field(description="Average confidence of pending items")
    oldest_pending_age_hours: Optional[float] = Field(
        None, description="Age of oldest pending item in hours"
    )
    by_entity_type: dict[str, int] = Field(
        default_factory=dict, description="Pending items by entity type"
    )


# =============================================================================
# Merge History Schemas
# =============================================================================


class MergeHistoryItemResponse(BaseModel):
    """Response model for a merge history item."""

    id: UUID = Field(description="History record ID")
    event_id: UUID = Field(description="Domain event ID")
    event_type: MergeEventType = Field(description="Type of merge operation")
    canonical_entity: Optional[EntitySummary] = Field(
        None, description="Canonical entity (for merges)"
    )
    affected_entity_ids: list[UUID] = Field(description="All entities involved")
    merge_reason: Optional[str] = Field(None, description="Reason for the operation")
    similarity_scores: Optional[dict] = Field(None, description="Similarity scores at time of merge")
    performed_by_name: Optional[str] = Field(None, description="User who performed operation")
    performed_at: datetime = Field(description="When operation occurred")
    undone: bool = Field(description="Whether this merge has been undone")
    undone_at: Optional[datetime] = Field(None, description="When merge was undone")
    undone_by_name: Optional[str] = Field(None, description="User who undid merge")
    undo_reason: Optional[str] = Field(None, description="Reason for undoing")
    can_undo: bool = Field(description="Whether this operation can be undone")

    class Config:
        from_attributes = True


class MergeHistoryListResponse(BaseModel):
    """Paginated list of merge history items."""

    items: list[MergeHistoryItemResponse] = Field(
        default_factory=list, description="List of history items"
    )
    total: int = Field(description="Total number of items")
    page: int = Field(description="Current page number (1-indexed)")
    page_size: int = Field(description="Items per page")
    pages: int = Field(description="Total number of pages")
    has_next: bool = Field(description="Whether there are more pages")
    has_prev: bool = Field(description="Whether there are previous pages")


# =============================================================================
# Configuration Schemas
# =============================================================================


class FeatureWeightConfig(BaseModel):
    """Feature weight configuration."""

    jaro_winkler: float = Field(0.3, ge=0.0, le=1.0)
    normalized_exact: float = Field(0.4, ge=0.0, le=1.0)
    type_match: float = Field(0.2, ge=0.0, le=1.0)
    same_page_bonus: float = Field(0.1, ge=0.0, le=1.0)
    embedding_cosine: float = Field(0.5, ge=0.0, le=1.0)
    graph_neighborhood: float = Field(0.3, ge=0.0, le=1.0)


class ConsolidationConfigResponse(BaseModel):
    """Response model for tenant consolidation configuration."""

    tenant_id: UUID = Field(description="Tenant ID")
    auto_merge_threshold: float = Field(
        ge=0.0, le=1.0, description="Threshold for automatic merging"
    )
    review_threshold: float = Field(
        ge=0.0, le=1.0, description="Threshold for queueing human review"
    )
    max_block_size: int = Field(gt=0, description="Maximum entities per blocking group")
    enable_embedding_similarity: bool = Field(
        description="Whether to compute embedding similarity"
    )
    enable_graph_similarity: bool = Field(
        description="Whether to compute graph neighborhood similarity"
    )
    enable_auto_consolidation: bool = Field(
        description="Whether to run consolidation on new entity extraction"
    )
    embedding_model: str = Field(description="Embedding model to use")
    feature_weights: dict = Field(description="Feature weights for scoring")
    created_at: datetime = Field(description="When config was created")
    updated_at: Optional[datetime] = Field(None, description="When config was last updated")

    class Config:
        from_attributes = True


class ConsolidationConfigRequest(BaseModel):
    """Request to update consolidation configuration."""

    auto_merge_threshold: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Threshold for automatic merging"
    )
    review_threshold: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Threshold for queueing human review"
    )
    max_block_size: Optional[int] = Field(
        None, gt=0, description="Maximum entities per blocking group"
    )
    enable_embedding_similarity: Optional[bool] = Field(
        None, description="Whether to compute embedding similarity"
    )
    enable_graph_similarity: Optional[bool] = Field(
        None, description="Whether to compute graph neighborhood similarity"
    )
    enable_auto_consolidation: Optional[bool] = Field(
        None, description="Whether to run consolidation on new entity extraction"
    )
    embedding_model: Optional[str] = Field(
        None, description="Embedding model to use", min_length=1, max_length=255
    )
    feature_weights: Optional[dict] = Field(
        None, description="Feature weights for scoring"
    )

    @field_validator("feature_weights")
    @classmethod
    def validate_feature_weights(cls, v: Optional[dict]) -> Optional[dict]:
        """Validate feature weight values."""
        if v is None:
            return v
        for key, value in v.items():
            if not isinstance(value, (int, float)):
                raise ValueError(f"Feature weight '{key}' must be a number")
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"Feature weight '{key}' must be between 0.0 and 1.0")
        return v


# =============================================================================
# Batch Operation Schemas
# =============================================================================


class BatchConsolidationRequest(BaseModel):
    """Request to run batch consolidation."""

    entity_type: Optional[str] = Field(
        None, description="Filter by entity type (all types if not specified)"
    )
    min_confidence: float = Field(
        0.9,
        ge=0.0,
        le=1.0,
        description="Minimum confidence for auto-merge",
    )
    dry_run: bool = Field(
        False, description="If true, only report what would be merged without executing"
    )
    max_merges: int = Field(
        1000, ge=1, le=10000, description="Maximum number of merges to execute"
    )


class BatchConsolidationResponse(BaseModel):
    """Response from batch consolidation."""

    job_id: UUID = Field(description="Background job ID")
    status: str = Field(description="Job status")
    dry_run: bool = Field(description="Whether this was a dry run")
    merges_executed: int = Field(0, description="Number of merges executed")
    merges_skipped: int = Field(0, description="Number of merges skipped")
    errors: list[str] = Field(default_factory=list, description="Any errors encountered")
    message: Optional[str] = Field(None, description="Status message")
