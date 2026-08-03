"""
Entity consolidation service module.

This module provides services for identifying and merging duplicate
entities in the knowledge graph. The consolidation pipeline has
multiple stages:

Stage 1 - Blocking (this module):
    Generates candidate pairs efficiently using blocking keys
    to avoid O(n^2) pairwise comparisons.

Stage 2 - Fast Similarity (this module):
    Computes fast string-based and phonetic similarity metrics
    to filter candidates.

Stage 3 - Deep Similarity (P3-003, P3-004):
    Computes embedding and graph-based similarity for
    candidates that pass Stage 2.

Stage 4 - Decision (P3-005):
    Combines all scores and makes merge/review/reject decisions.

Stage 5 - Execution (this module):
    Executes merges, creates aliases, and emits events.

Example usage:
    from kg_builder.services.consolidation import (
        BlockingEngine,
        StringSimilarityService,
        MergeService,
    )

    # Find candidates using blocking
    engine = BlockingEngine(max_block_size=500)
    blocking_result = await engine.find_candidates(session, entity, tenant_id)

    # Compute string similarity and filter
    matcher = StringSimilarityService()
    candidates_with_scores = matcher.filter_candidates(
        entity,
        blocking_result.candidates,
        threshold=0.70,
    )

    # Candidates passing threshold proceed to Stage 3
    for candidate, scores in candidates_with_scores:
        # Compute embedding similarity, graph similarity, etc.
        pass

    # Execute merge for high-confidence candidates
    merge_service = MergeService(session, event_bus)
    result = await merge_service.merge_entities(
        canonical_entity=entity,
        merged_entities=[candidate],
        tenant_id=tenant_id,
        merge_reason="auto_high_confidence",
        similarity_scores=scores.to_dict(),
    )
"""

from kg_builder.services.consolidation.blocking import (
    BlockingEngine,
    BlockingResult,
    BlockingStrategy,
)
from kg_builder.services.consolidation.combined_scoring import (
    CombinedScoringPipeline,
    FeatureWeights,
    ScoringResult,
    create_default_config_for_scoring,
)
from kg_builder.services.consolidation.embedding_similarity import (
    EmbeddingSimilarityService,
    cosine_similarity,
    euclidean_similarity,
    get_embedding_similarity_service,
)
from kg_builder.services.consolidation.graph_similarity import (
    GraphNeighborhood,
    GraphSimilarityService,
    get_graph_similarity_service,
)
from kg_builder.services.consolidation.merge_service import (
    DEFAULT_PROPERTY_STRATEGIES,
    EntitySplitError,
    MergeAuthorizationError,
    MergeError,
    MergeResult,
    MergeService,
    MergeUndoError,
    MergeValidationError,
    PropertyMergeStrategy,
    SplitResult,
    UndoResult,
    deep_merge_dicts,
    merge_property,
)
from kg_builder.services.consolidation.string_similarity import (
    StringSimilarityService,
    compute_phonetic_similarity,
    compute_string_similarity,
)

__all__ = [
    # Blocking (Stage 1)
    "BlockingEngine",
    "BlockingResult",
    "BlockingStrategy",
    # String Similarity (Stage 2)
    "StringSimilarityService",
    "compute_string_similarity",
    "compute_phonetic_similarity",
    # Embedding Similarity (Stage 3)
    "EmbeddingSimilarityService",
    "cosine_similarity",
    "euclidean_similarity",
    "get_embedding_similarity_service",
    # Graph Similarity (Stage 3)
    "GraphSimilarityService",
    "GraphNeighborhood",
    "get_graph_similarity_service",
    # Combined Scoring (Stage 4)
    "CombinedScoringPipeline",
    "FeatureWeights",
    "ScoringResult",
    "create_default_config_for_scoring",
    # Merge Service (Stage 5)
    "MergeService",
    "MergeResult",
    "MergeError",
    "MergeValidationError",
    "MergeAuthorizationError",
    "MergeUndoError",
    "EntitySplitError",
    "PropertyMergeStrategy",
    "DEFAULT_PROPERTY_STRATEGIES",
    "merge_property",
    "deep_merge_dicts",
    # Undo / Split
    "UndoResult",
    "SplitResult",
]
