# Schemas package

from kg_builder.schemas.consolidation import (
    # Batch Operations
    BatchConsolidationRequest,
    BatchConsolidationResponse,
    ComputeCandidatesRequest,
    ComputeCandidatesResponse,
    ConsolidationConfigRequest,
    ConsolidationConfigResponse,
    # Entity Summary
    EntitySummary,
    # Configuration
    FeatureWeightConfig,
    MergeCandidateListResponse,
    MergeCandidateResponse,
    # Enums
    MergeDecision,
    MergeEventType,
    # Merge History
    MergeHistoryItemResponse,
    MergeHistoryListResponse,
    # Merge Operations
    MergeRequest,
    MergeResponse,
    ReviewDecision,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    # Review Queue
    ReviewQueueItemResponse,
    ReviewQueueListResponse,
    ReviewQueueStatsResponse,
    ReviewStatus,
    # Merge Candidates
    SimilarityBreakdown,
    SplitEntityRequest,
    SplitEntityResponse,
    UndoMergeRequest,
    UndoMergeResponse,
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
