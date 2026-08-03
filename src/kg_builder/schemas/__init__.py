# Schemas package

from kg_builder.schemas.consolidation import (
    # Enums
    MergeDecision,
    ReviewDecision,
    ReviewStatus,
    MergeEventType,
    # Entity Summary
    EntitySummary,
    # Merge Candidates
    SimilarityBreakdown,
    MergeCandidateResponse,
    MergeCandidateListResponse,
    ComputeCandidatesRequest,
    ComputeCandidatesResponse,
    # Merge Operations
    MergeRequest,
    MergeResponse,
    UndoMergeRequest,
    UndoMergeResponse,
    SplitEntityRequest,
    SplitEntityResponse,
    # Review Queue
    ReviewQueueItemResponse,
    ReviewQueueListResponse,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    ReviewQueueStatsResponse,
    # Merge History
    MergeHistoryItemResponse,
    MergeHistoryListResponse,
    # Configuration
    FeatureWeightConfig,
    ConsolidationConfigResponse,
    ConsolidationConfigRequest,
    # Batch Operations
    BatchConsolidationRequest,
    BatchConsolidationResponse,
)

__all__ = [
    # Enums
    "MergeDecision",
    "ReviewDecision",
    "ReviewStatus",
    "MergeEventType",
    # Entity Summary
    "EntitySummary",
    # Merge Candidates
    "SimilarityBreakdown",
    "MergeCandidateResponse",
    "MergeCandidateListResponse",
    "ComputeCandidatesRequest",
    "ComputeCandidatesResponse",
    # Merge Operations
    "MergeRequest",
    "MergeResponse",
    "UndoMergeRequest",
    "UndoMergeResponse",
    "SplitEntityRequest",
    "SplitEntityResponse",
    # Review Queue
    "ReviewQueueItemResponse",
    "ReviewQueueListResponse",
    "ReviewDecisionRequest",
    "ReviewDecisionResponse",
    "ReviewQueueStatsResponse",
    # Merge History
    "MergeHistoryItemResponse",
    "MergeHistoryListResponse",
    # Configuration
    "FeatureWeightConfig",
    "ConsolidationConfigResponse",
    "ConsolidationConfigRequest",
    # Batch Operations
    "BatchConsolidationRequest",
    "BatchConsolidationResponse",
]
