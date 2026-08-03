"""
Combined similarity scoring pipeline for entity consolidation.

This module combines multiple similarity signals into a final confidence
score for merge decisions. It orchestrates:
- String similarity (Stage 2 - fast)
- Phonetic similarity (Stage 2 - fast)
- Embedding similarity (Stage 3 - semantic)
- Graph neighborhood similarity (Stage 3 - structural)

The pipeline uses configurable weights from ConsolidationConfig to
produce a final combined score and decision classification.

Example usage:
    >>> pipeline = CombinedScoringPipeline(
    ...     embedding_similarity=emb_service,
    ...     graph_similarity=graph_service,
    ...     config=tenant_config,
    ... )
    >>> result = await pipeline.compute_combined_score(
    ...     entity_a, entity_b, string_scores, tenant_id
    ... )
    >>> print(f"Score: {result.combined_score:.3f} ({result.classification})")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from kg_builder.schemas.similarity import (
    SimilarityScores,
)

if TYPE_CHECKING:
    from kg_builder.models.consolidation_config import ConsolidationConfig
    from kg_builder.models.extracted_entity import ExtractedEntity
    from kg_builder.services.consolidation.embedding_similarity import EmbeddingSimilarityService
    from kg_builder.services.consolidation.graph_similarity import GraphSimilarityService

logger = logging.getLogger(__name__)


# Default weights for features
DEFAULT_FEATURE_WEIGHTS = {
    "jaro_winkler": 0.15,
    "normalized_exact": 0.20,
    "type_match": 0.10,
    "embedding_cosine": 0.35,
    "graph_neighborhood": 0.20,
}


@dataclass
class FeatureWeights:
    """
    Configurable weights for similarity features.

    Weights control how individual similarity scores are combined
    into the final score. Higher weights give more influence.

    Attributes:
        jaro_winkler: Weight for Jaro-Winkler string similarity
        normalized_exact: Weight for exact normalized match
        type_match: Weight for entity type match
        embedding_cosine: Weight for embedding cosine similarity
        graph_neighborhood: Weight for graph neighborhood similarity
    """

    # String-based features (Phase 1 / Stage 2)
    jaro_winkler: float = 0.15
    normalized_exact: float = 0.20
    type_match: float = 0.10

    # Semantic features (Phase 3 / Stage 3)
    embedding_cosine: float = 0.35
    graph_neighborhood: float = 0.20

    @classmethod
    def from_config(cls, config: ConsolidationConfig) -> FeatureWeights:
        """
        Create weights from tenant configuration.

        Args:
            config: Tenant consolidation configuration

        Returns:
            FeatureWeights with values from config
        """
        weights_dict = config.feature_weights or {}
        return cls(
            jaro_winkler=weights_dict.get("jaro_winkler", 0.15),
            normalized_exact=weights_dict.get("normalized_exact", 0.20),
            type_match=weights_dict.get("type_match", 0.10),
            embedding_cosine=weights_dict.get("embedding_cosine", 0.35),
            graph_neighborhood=weights_dict.get("graph_neighborhood", 0.20),
        )

    @classmethod
    def default(cls) -> FeatureWeights:
        """Return default weight configuration."""
        return cls()

    def normalize(self, enabled_features: set[str]) -> dict[str, float]:
        """
        Get normalized weights for enabled features only.

        Redistributes weights so enabled features sum to 1.0.

        Args:
            enabled_features: Set of feature names that are enabled

        Returns:
            Dict mapping feature name to normalized weight
        """
        all_weights = {
            "jaro_winkler": self.jaro_winkler,
            "normalized_exact": self.normalized_exact,
            "type_match": self.type_match,
            "embedding_cosine": self.embedding_cosine,
            "graph_neighborhood": self.graph_neighborhood,
        }

        enabled_weights = {k: v for k, v in all_weights.items() if k in enabled_features}

        if not enabled_weights:
            return {}

        total = sum(enabled_weights.values())
        if total == 0:
            # Equal weights if all zero
            return {k: 1.0 / len(enabled_weights) for k in enabled_weights}

        return {k: v / total for k, v in enabled_weights.items()}

    def to_dict(self) -> dict[str, float]:
        """Convert to dictionary."""
        return {
            "jaro_winkler": self.jaro_winkler,
            "normalized_exact": self.normalized_exact,
            "type_match": self.type_match,
            "embedding_cosine": self.embedding_cosine,
            "graph_neighborhood": self.graph_neighborhood,
        }


@dataclass
class ScoringResult:
    """
    Result of combined similarity scoring.

    Contains all individual scores, the combined score, and
    the classification decision.

    Attributes:
        entity_a_id: First entity ID
        entity_b_id: Second entity ID
        jaro_winkler: Jaro-Winkler similarity score
        normalized_exact: Exact normalized match score
        type_match: Type match score
        embedding_cosine: Embedding cosine similarity
        graph_neighborhood: Graph neighborhood similarity
        combined_score: Final weighted combination
        classification: Decision classification (high, medium, low)
        weights_used: The weights that were applied
        computation_time_ms: Total computation time
    """

    entity_a_id: UUID
    entity_b_id: UUID

    # Individual scores (all 0.0-1.0 range)
    jaro_winkler: float | None = None
    normalized_exact: float | None = None
    type_match: float | None = None
    embedding_cosine: float | None = None
    graph_neighborhood: float | None = None

    # Combined result
    combined_score: float = 0.0

    # Classification
    classification: str = "low"  # "high", "medium", or "low"

    # Metadata
    weights_used: dict[str, float] = field(default_factory=dict)
    computation_time_ms: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary for storage/transmission."""
        return {
            "entity_a_id": str(self.entity_a_id),
            "entity_b_id": str(self.entity_b_id),
            "scores": {
                "jaro_winkler": self.jaro_winkler,
                "normalized_exact": self.normalized_exact,
                "type_match": self.type_match,
                "embedding_cosine": self.embedding_cosine,
                "graph_neighborhood": self.graph_neighborhood,
            },
            "combined_score": self.combined_score,
            "classification": self.classification,
            "weights_used": self.weights_used,
            "computation_time_ms": self.computation_time_ms,
        }

    def get_decision(self) -> str:
        """
        Get decision based on classification.

        Returns:
            Decision string: "auto_merge", "review", or "reject"
        """
        if self.classification == "high":
            return "auto_merge"
        elif self.classification == "medium":
            return "review"
        else:
            return "reject"


class CombinedScoringPipeline:
    """
    Pipeline for computing combined similarity scores.

    Orchestrates multiple similarity services and combines their
    outputs into a final confidence score for merge decisions.

    The pipeline:
    1. Takes pre-computed string/phonetic scores (from Stage 2)
    2. Optionally computes embedding similarity (if enabled)
    3. Optionally computes graph similarity (if enabled)
    4. Combines all scores using configured weights
    5. Classifies result as high/medium/low confidence

    Attributes:
        embedding_similarity: Service for embedding-based similarity
        graph_similarity: Service for graph-based similarity
        config: Tenant consolidation configuration
        weights: Feature weights for combination
    """

    def __init__(
        self,
        embedding_similarity: EmbeddingSimilarityService | None,
        graph_similarity: GraphSimilarityService | None,
        config: ConsolidationConfig,
    ):
        """
        Initialize combined scoring pipeline.

        Args:
            embedding_similarity: Service for embedding-based similarity (optional)
            graph_similarity: Service for graph-based similarity (optional)
            config: Tenant consolidation configuration
        """
        self._embedding_similarity = embedding_similarity
        self._graph_similarity = graph_similarity
        self._config = config
        self._weights = FeatureWeights.from_config(config)

    @property
    def config(self) -> ConsolidationConfig:
        """Get the configuration."""
        return self._config

    @property
    def weights(self) -> FeatureWeights:
        """Get the feature weights."""
        return self._weights

    async def compute_combined_score(
        self,
        entity_a: ExtractedEntity,
        entity_b: ExtractedEntity,
        string_scores: SimilarityScores,
        tenant_id: UUID,
    ) -> ScoringResult:
        """
        Compute combined similarity score for entity pair.

        Uses configurable weights to combine:
        - String similarity (from blocking phase)
        - Embedding similarity (if enabled)
        - Graph similarity (if enabled)

        Args:
            entity_a: First entity
            entity_b: Second entity
            string_scores: Pre-computed string similarity scores
            tenant_id: Tenant ID

        Returns:
            ScoringResult with combined score and classification
        """
        start_time = time.perf_counter()

        # Initialize result with string scores
        result = ScoringResult(
            entity_a_id=entity_a.id,
            entity_b_id=entity_b.id,
        )

        # Extract string scores
        if string_scores.string_scores.jaro_winkler:
            result.jaro_winkler = string_scores.string_scores.jaro_winkler.raw_score
        if string_scores.string_scores.normalized_exact:
            result.normalized_exact = string_scores.string_scores.normalized_exact.raw_score
        if string_scores.contextual.type_match:
            result.type_match = string_scores.contextual.type_match.raw_score

        # Determine enabled features
        enabled = set()
        if result.jaro_winkler is not None:
            enabled.add("jaro_winkler")
        if result.normalized_exact is not None:
            enabled.add("normalized_exact")
        if result.type_match is not None:
            enabled.add("type_match")

        # Compute embedding similarity if enabled
        if self._config.enable_embedding_similarity and self._embedding_similarity:
            try:
                result.embedding_cosine = await self._embedding_similarity.compute_similarity(
                    entity_a, entity_b, tenant_id
                )
                enabled.add("embedding_cosine")
            except Exception as e:
                logger.warning(f"Embedding similarity failed: {e}")

        # Compute graph similarity if enabled
        if self._config.enable_graph_similarity and self._graph_similarity:
            try:
                result.graph_neighborhood = await self._graph_similarity.compute_similarity(
                    entity_a.id, entity_b.id
                )
                enabled.add("graph_neighborhood")
            except Exception as e:
                logger.warning(f"Graph similarity failed: {e}")

        # Get normalized weights for enabled features
        weights = self._weights.normalize(enabled)
        result.weights_used = weights

        # Compute weighted sum
        score_values = {
            "jaro_winkler": result.jaro_winkler,
            "normalized_exact": result.normalized_exact,
            "type_match": result.type_match,
            "embedding_cosine": result.embedding_cosine,
            "graph_neighborhood": result.graph_neighborhood,
        }

        combined = 0.0
        for feature, weight in weights.items():
            value = score_values.get(feature)
            if value is not None:
                combined += value * weight

        result.combined_score = combined

        # Classify confidence
        result.classification = self._classify_confidence(combined)

        # Track computation time
        result.computation_time_ms = (time.perf_counter() - start_time) * 1000

        logger.debug(
            f"Combined score for {entity_a.name} <-> {entity_b.name}: "
            f"{combined:.3f} ({result.classification}) in {result.computation_time_ms:.2f}ms"
        )

        return result

    def _classify_confidence(self, score: float) -> str:
        """
        Classify score into confidence category.

        Uses thresholds from configuration.

        Args:
            score: Combined similarity score

        Returns:
            Classification: 'high', 'medium', or 'low'
        """
        if score >= self._config.auto_merge_threshold:
            return "high"
        elif score >= self._config.review_threshold:
            return "medium"
        else:
            return "low"

    async def compute_batch_scores(
        self,
        entity: ExtractedEntity,
        candidates: list[tuple[ExtractedEntity, SimilarityScores]],
        tenant_id: UUID,
    ) -> list[ScoringResult]:
        """
        Compute combined scores for entity vs multiple candidates.

        Optimized for batch processing with pre-fetched embeddings.

        Args:
            entity: Source entity
            candidates: List of (candidate_entity, string_scores) tuples
            tenant_id: Tenant ID

        Returns:
            List of ScoringResult sorted by combined_score descending
        """
        if not candidates:
            return []

        start_time = time.perf_counter()
        results = []

        # Batch embedding computation if enabled
        embedding_scores: dict[UUID, float] = {}
        if self._config.enable_embedding_similarity and self._embedding_similarity:
            try:
                candidate_entities = [c[0] for c in candidates]
                batch_results = await self._embedding_similarity.compute_similarities_batch(
                    entity, candidate_entities, tenant_id
                )
                embedding_scores = {e.id: score for e, score in batch_results}
            except Exception as e:
                logger.warning(f"Batch embedding similarity failed: {e}")

        # Batch graph similarity if enabled
        graph_scores: dict[UUID, float] = {}
        if self._config.enable_graph_similarity and self._graph_similarity:
            try:
                candidate_ids = [c[0].id for c in candidates]
                graph_scores = await self._graph_similarity.compute_similarities_batch(
                    entity.id, candidate_ids
                )
            except Exception as e:
                logger.warning(f"Batch graph similarity failed: {e}")

        # Compute combined scores
        for candidate, string_scores in candidates:
            result = ScoringResult(
                entity_a_id=entity.id,
                entity_b_id=candidate.id,
            )

            # Extract string scores
            if string_scores.string_scores.jaro_winkler:
                result.jaro_winkler = string_scores.string_scores.jaro_winkler.raw_score
            if string_scores.string_scores.normalized_exact:
                result.normalized_exact = string_scores.string_scores.normalized_exact.raw_score
            if string_scores.contextual.type_match:
                result.type_match = string_scores.contextual.type_match.raw_score

            # Add embedding and graph scores from batch
            result.embedding_cosine = embedding_scores.get(candidate.id)
            result.graph_neighborhood = graph_scores.get(candidate.id)

            # Determine enabled features
            enabled = set()
            if result.jaro_winkler is not None:
                enabled.add("jaro_winkler")
            if result.normalized_exact is not None:
                enabled.add("normalized_exact")
            if result.type_match is not None:
                enabled.add("type_match")
            if result.embedding_cosine is not None:
                enabled.add("embedding_cosine")
            if result.graph_neighborhood is not None:
                enabled.add("graph_neighborhood")

            # Compute weighted combination
            weights = self._weights.normalize(enabled)
            result.weights_used = weights

            score_values = {
                "jaro_winkler": result.jaro_winkler,
                "normalized_exact": result.normalized_exact,
                "type_match": result.type_match,
                "embedding_cosine": result.embedding_cosine,
                "graph_neighborhood": result.graph_neighborhood,
            }

            combined = sum((score_values.get(f) or 0) * w for f, w in weights.items())

            result.combined_score = combined
            result.classification = self._classify_confidence(combined)
            results.append(result)

        # Sort by combined score descending
        results.sort(key=lambda r: r.combined_score, reverse=True)

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.debug(f"Batch combined scores: {len(results)} candidates in {elapsed_ms:.2f}ms")

        return results

    def route_decision(
        self,
        result: ScoringResult,
    ) -> str:
        """
        Route result to appropriate action.

        Args:
            result: Scoring result

        Returns:
            Action: "auto_merge", "review", or "reject"
        """
        return result.get_decision()

    async def process_candidate_pair(
        self,
        entity_a: ExtractedEntity,
        entity_b: ExtractedEntity,
        string_scores: SimilarityScores,
        tenant_id: UUID,
    ) -> tuple[ScoringResult, str]:
        """
        Process a candidate pair and return result with decision.

        Convenience method that combines scoring and routing.

        Args:
            entity_a: First entity
            entity_b: Second entity
            string_scores: Pre-computed string scores
            tenant_id: Tenant ID

        Returns:
            Tuple of (ScoringResult, decision_action)
        """
        result = await self.compute_combined_score(entity_a, entity_b, string_scores, tenant_id)
        decision = self.route_decision(result)
        return result, decision


def create_default_config_for_scoring() -> dict:
    """
    Create a default configuration dictionary for scoring.

    Useful for testing or when no tenant config is available.

    Returns:
        Dict with default configuration values
    """
    return {
        "auto_merge_threshold": 0.90,
        "review_threshold": 0.50,
        "enable_embedding_similarity": True,
        "enable_graph_similarity": True,
        "feature_weights": DEFAULT_FEATURE_WEIGHTS.copy(),
    }
