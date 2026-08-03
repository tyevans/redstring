"""
Embedding-based similarity computation for entity consolidation.

This module computes semantic similarity between entities using
sentence embeddings (bge-m3 via Ollama) and cosine similarity.

The EmbeddingSimilarityService integrates with:
- OllamaEmbeddingService: For computing embeddings
- EmbeddingCache: For caching embeddings in Redis
- PostgreSQL pgvector: For storing embeddings long-term

This is Stage 3 of the consolidation pipeline, providing semantic
similarity scores for candidates that passed Stage 2 (string similarity).

Example usage:
    >>> service = EmbeddingSimilarityService(embedding_service, cache)
    >>> similarity = await service.compute_similarity(entity_a, entity_b, tenant_id)
    >>> print(f"Semantic similarity: {similarity:.3f}")
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING
from uuid import UUID

import numpy as np
from numpy.linalg import norm

from kg_builder.schemas.similarity import (
    SimilarityScore,
    SimilarityType,
    SemanticSimilarityScores,
)

if TYPE_CHECKING:
    from kg_builder.models.extracted_entity import ExtractedEntity
    from kg_builder.services.embedding import OllamaEmbeddingService
    from kg_builder.services.embedding_cache import EmbeddingCache

logger = logging.getLogger(__name__)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """
    Compute cosine similarity between two vectors.

    Args:
        a: First vector
        b: Second vector

    Returns:
        Cosine similarity in range [-1, 1]
    """
    norm_a = norm(a)
    norm_b = norm(b)

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return float(np.dot(a, b) / (norm_a * norm_b))


def euclidean_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """
    Compute Euclidean similarity (1 / (1 + distance)).

    Args:
        a: First vector
        b: Second vector

    Returns:
        Euclidean similarity in range [0, 1]
    """
    distance = np.linalg.norm(a - b)
    return 1.0 / (1.0 + distance)


class EmbeddingSimilarityService:
    """
    Service for computing semantic similarity between entities.

    Uses Ollama with bge-m3 embeddings and cosine similarity
    to measure semantic relatedness of entity representations.

    Attributes:
        embedding_service: Ollama service for generating embeddings
        embedding_cache: Redis cache for embeddings
    """

    def __init__(
        self,
        embedding_service: OllamaEmbeddingService,
        embedding_cache: EmbeddingCache | None = None,
        max_description_length: int = 500,
    ):
        """
        Initialize embedding similarity service.

        Args:
            embedding_service: Ollama service for generating bge-m3 embeddings
            embedding_cache: Optional cache for storing embeddings
            max_description_length: Maximum description length to include
        """
        self._embedding_service = embedding_service
        self._embedding_cache = embedding_cache
        self._max_description_length = max_description_length

    def entity_to_text(self, entity: ExtractedEntity) -> str:
        """
        Convert entity to text representation for embedding.

        Combines name, type, and description into a single text
        suitable for embedding. The text representation is designed
        to capture the entity's identity and context.

        Args:
            entity: Entity to convert

        Returns:
            Text representation suitable for embedding
        """
        parts = []

        # Name is always included (most important)
        parts.append(entity.name)

        # Include type for context
        if entity.entity_type:
            parts.append(f"[{entity.entity_type}]")

        # Include description if available (truncated)
        if entity.description:
            desc = entity.description[: self._max_description_length]
            if len(entity.description) > self._max_description_length:
                desc += "..."
            parts.append(desc)

        return " ".join(parts)

    async def get_embedding(
        self,
        entity: ExtractedEntity,
        tenant_id: UUID,
        use_cache: bool = True,
    ) -> np.ndarray:
        """
        Get embedding for entity, using cache if available.

        First checks the cache, then computes if needed. If computed,
        caches the result for future use.

        Args:
            entity: Entity to embed
            tenant_id: Tenant ID for cache key
            use_cache: Whether to use cache (default True)

        Returns:
            Embedding vector as numpy array
        """
        # Try cache first
        if use_cache and self._embedding_cache is not None:
            cached = await self._embedding_cache.get(tenant_id, entity.id)
            if cached is not None:
                return cached

        # Compute embedding
        text = self.entity_to_text(entity)
        embedding = await self._embedding_service.encode(text)

        # Cache result
        if use_cache and self._embedding_cache is not None:
            await self._embedding_cache.set(tenant_id, entity.id, embedding)

        return embedding

    async def compute_similarity(
        self,
        entity_a: ExtractedEntity,
        entity_b: ExtractedEntity,
        tenant_id: UUID,
        use_cache: bool = True,
    ) -> float:
        """
        Compute embedding similarity between two entities.

        Uses cosine similarity and normalizes to [0, 1] range.

        Args:
            entity_a: First entity
            entity_b: Second entity
            tenant_id: Tenant ID for cache
            use_cache: Whether to use cache

        Returns:
            Similarity score in range [0, 1]
        """
        # Get embeddings
        emb_a = await self.get_embedding(entity_a, tenant_id, use_cache)
        emb_b = await self.get_embedding(entity_b, tenant_id, use_cache)

        # Compute cosine similarity
        similarity = cosine_similarity(emb_a, emb_b)

        # Normalize from [-1, 1] to [0, 1]
        # Most embeddings will have positive similarity, but we handle full range
        normalized = (similarity + 1) / 2

        logger.debug(
            f"Embedding similarity: {entity_a.name} <-> {entity_b.name} = {normalized:.3f}"
        )

        return normalized

    async def compute_similarity_scores(
        self,
        entity_a: ExtractedEntity,
        entity_b: ExtractedEntity,
        tenant_id: UUID,
        use_cache: bool = True,
    ) -> SemanticSimilarityScores:
        """
        Compute full semantic similarity scores for entity pair.

        Returns SemanticSimilarityScores with both cosine and euclidean
        similarity for comprehensive scoring.

        Args:
            entity_a: First entity
            entity_b: Second entity
            tenant_id: Tenant ID for cache
            use_cache: Whether to use cache

        Returns:
            SemanticSimilarityScores with cosine and euclidean similarity
        """
        start_time = time.perf_counter()

        # Get embeddings
        emb_a = await self.get_embedding(entity_a, tenant_id, use_cache)
        emb_b = await self.get_embedding(entity_b, tenant_id, use_cache)

        # Compute cosine similarity
        cosine = cosine_similarity(emb_a, emb_b)
        cosine_normalized = (cosine + 1) / 2

        # Compute euclidean similarity
        euclidean = euclidean_similarity(emb_a, emb_b)

        computation_time_ms = (time.perf_counter() - start_time) * 1000

        scores = SemanticSimilarityScores(
            embedding_cosine=SimilarityScore(
                similarity_type=SimilarityType.EMBEDDING_COSINE,
                raw_score=cosine_normalized,
                computation_time_ms=computation_time_ms,
            ),
            embedding_euclidean=SimilarityScore(
                similarity_type=SimilarityType.EMBEDDING_EUCLIDEAN,
                raw_score=euclidean,
                computation_time_ms=computation_time_ms,
            ),
        )

        logger.debug(
            f"Semantic similarity computed for ({entity_a.id}, {entity_b.id}): "
            f"cosine={cosine_normalized:.3f}, euclidean={euclidean:.3f}, "
            f"time={computation_time_ms:.2f}ms"
        )

        return scores

    async def compute_similarities_batch(
        self,
        entity: ExtractedEntity,
        candidates: list[ExtractedEntity],
        tenant_id: UUID,
        use_cache: bool = True,
    ) -> list[tuple[ExtractedEntity, float]]:
        """
        Compute similarity between entity and multiple candidates.

        Optimized for batch processing with cache.

        Args:
            entity: Source entity
            candidates: List of candidate entities
            tenant_id: Tenant ID
            use_cache: Whether to use cache

        Returns:
            List of (candidate, similarity) tuples sorted by similarity descending
        """
        if not candidates:
            return []

        start_time = time.perf_counter()

        # Get source embedding
        source_emb = await self.get_embedding(entity, tenant_id, use_cache)

        # Batch get candidate embeddings from cache
        cached_embeddings: dict[UUID, np.ndarray | None] = {}
        if use_cache and self._embedding_cache is not None:
            candidate_ids = [c.id for c in candidates]
            cached_embeddings = await self._embedding_cache.get_batch(
                tenant_id, candidate_ids
            )

        # Compute missing embeddings
        to_compute = []
        for candidate in candidates:
            if cached_embeddings.get(candidate.id) is None:
                to_compute.append(candidate)

        if to_compute:
            # Batch encode missing embeddings
            texts = [self.entity_to_text(c) for c in to_compute]
            new_embeddings = await self._embedding_service.encode_batch(texts)

            # Cache new embeddings
            embeddings_to_cache: dict[UUID, np.ndarray] = {}
            for i, candidate in enumerate(to_compute):
                cached_embeddings[candidate.id] = new_embeddings[i]
                embeddings_to_cache[candidate.id] = new_embeddings[i]

            if use_cache and self._embedding_cache is not None:
                await self._embedding_cache.set_batch(tenant_id, embeddings_to_cache)

        # Compute similarities
        results = []
        for candidate in candidates:
            candidate_emb = cached_embeddings.get(candidate.id)
            if candidate_emb is not None:
                similarity = cosine_similarity(source_emb, candidate_emb)
                # Normalize to [0, 1]
                normalized = (similarity + 1) / 2
                results.append((candidate, normalized))

        # Sort by similarity descending
        results.sort(key=lambda x: x[1], reverse=True)

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        cache_hits = len(candidates) - len(to_compute)
        logger.debug(
            f"Batch similarity for {entity.name}: "
            f"computed {len(results)} candidates, "
            f"cache hits={cache_hits}, "
            f"time={elapsed_ms:.2f}ms"
        )

        return results

    async def compute_batch_scores(
        self,
        entity: ExtractedEntity,
        candidates: list[ExtractedEntity],
        tenant_id: UUID,
        use_cache: bool = True,
    ) -> list[tuple[ExtractedEntity, SemanticSimilarityScores]]:
        """
        Compute full semantic similarity scores for entity vs multiple candidates.

        Returns SemanticSimilarityScores for each candidate.

        Args:
            entity: Source entity
            candidates: List of candidate entities
            tenant_id: Tenant ID
            use_cache: Whether to use cache

        Returns:
            List of (candidate, scores) tuples sorted by cosine similarity descending
        """
        if not candidates:
            return []

        start_time = time.perf_counter()

        # Get source embedding
        source_emb = await self.get_embedding(entity, tenant_id, use_cache)

        # Batch get candidate embeddings from cache
        cached_embeddings: dict[UUID, np.ndarray | None] = {}
        if use_cache and self._embedding_cache is not None:
            candidate_ids = [c.id for c in candidates]
            cached_embeddings = await self._embedding_cache.get_batch(
                tenant_id, candidate_ids
            )

        # Compute missing embeddings
        to_compute = []
        for candidate in candidates:
            if cached_embeddings.get(candidate.id) is None:
                to_compute.append(candidate)

        if to_compute:
            texts = [self.entity_to_text(c) for c in to_compute]
            new_embeddings = await self._embedding_service.encode_batch(texts)

            embeddings_to_cache: dict[UUID, np.ndarray] = {}
            for i, candidate in enumerate(to_compute):
                cached_embeddings[candidate.id] = new_embeddings[i]
                embeddings_to_cache[candidate.id] = new_embeddings[i]

            if use_cache and self._embedding_cache is not None:
                await self._embedding_cache.set_batch(tenant_id, embeddings_to_cache)

        # Compute similarity scores
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        results = []
        for candidate in candidates:
            candidate_emb = cached_embeddings.get(candidate.id)
            if candidate_emb is not None:
                cosine = cosine_similarity(source_emb, candidate_emb)
                cosine_normalized = (cosine + 1) / 2
                euclidean = euclidean_similarity(source_emb, candidate_emb)

                scores = SemanticSimilarityScores(
                    embedding_cosine=SimilarityScore(
                        similarity_type=SimilarityType.EMBEDDING_COSINE,
                        raw_score=cosine_normalized,
                        computation_time_ms=elapsed_ms / len(candidates),
                    ),
                    embedding_euclidean=SimilarityScore(
                        similarity_type=SimilarityType.EMBEDDING_EUCLIDEAN,
                        raw_score=euclidean,
                        computation_time_ms=elapsed_ms / len(candidates),
                    ),
                )
                results.append((candidate, scores))

        # Sort by cosine similarity descending
        results.sort(
            key=lambda x: (
                x[1].embedding_cosine.raw_score if x[1].embedding_cosine else 0.0
            ),
            reverse=True,
        )

        cache_hits = len(candidates) - len(to_compute)
        logger.debug(
            f"Batch scores for {entity.name}: "
            f"computed {len(results)} candidates, "
            f"cache hits={cache_hits}, "
            f"time={elapsed_ms:.2f}ms"
        )

        return results

    async def invalidate_entity_embedding(
        self,
        entity_id: UUID,
        tenant_id: UUID,
    ) -> None:
        """
        Invalidate cached embedding when entity is updated.

        Should be called when entity name, description, or type changes.

        Args:
            entity_id: Entity ID
            tenant_id: Tenant ID
        """
        if self._embedding_cache is not None:
            await self._embedding_cache.invalidate(tenant_id, entity_id)
            logger.debug(f"Invalidated embedding cache for entity {entity_id}")


async def get_embedding_similarity_service() -> EmbeddingSimilarityService | None:
    """
    Get embedding similarity service with dependencies.

    Creates the service with embedding service and cache from
    application singletons.

    Returns:
        EmbeddingSimilarityService or None if embedding service unavailable
    """
    from kg_builder.services.embedding import get_embedding_service
    from kg_builder.services.embedding_cache import get_embedding_cache

    try:
        embedding_service = get_embedding_service()
        embedding_cache = await get_embedding_cache()

        return EmbeddingSimilarityService(
            embedding_service=embedding_service,
            embedding_cache=embedding_cache,
        )
    except Exception as e:
        logger.error(f"Failed to create embedding similarity service: {e}")
        return None
