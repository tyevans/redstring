"""
Unit tests for EmbeddingSimilarityService.

Tests the embedding-based similarity computation with mocked services.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import numpy as np
import pytest

from kg_builder.schemas.similarity import SimilarityType
from kg_builder.services.consolidation.embedding_similarity import (
    EmbeddingSimilarityService,
    cosine_similarity,
    euclidean_similarity,
    get_embedding_similarity_service,
)


class TestCosineSimilarity:
    """Tests for cosine similarity function."""

    def test_identical_vectors(self):
        """Test identical vectors have similarity 1.0."""
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([1.0, 0.0, 0.0])

        result = cosine_similarity(a, b)

        assert result == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        """Test orthogonal vectors have similarity 0.0."""
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 1.0, 0.0])

        result = cosine_similarity(a, b)

        assert result == pytest.approx(0.0)

    def test_opposite_vectors(self):
        """Test opposite vectors have similarity -1.0."""
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([-1.0, 0.0, 0.0])

        result = cosine_similarity(a, b)

        assert result == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self):
        """Test zero vector returns 0.0 similarity."""
        a = np.zeros(3)
        b = np.array([1.0, 0.0, 0.0])

        result = cosine_similarity(a, b)

        assert result == 0.0

    def test_both_zero_vectors_returns_zero(self):
        """Test both zero vectors returns 0.0."""
        a = np.zeros(3)
        b = np.zeros(3)

        result = cosine_similarity(a, b)

        assert result == 0.0

    def test_similar_vectors(self):
        """Test similar vectors have high similarity."""
        a = np.array([1.0, 0.1, 0.0])
        b = np.array([1.0, 0.0, 0.1])

        result = cosine_similarity(a, b)

        assert result > 0.9

    def test_high_dimensional_vectors(self):
        """Test with high-dimensional vectors like embeddings."""
        np.random.seed(42)
        a = np.random.randn(1024).astype(np.float32)
        b = a + 0.01 * np.random.randn(1024).astype(np.float32)  # Slightly perturbed

        result = cosine_similarity(a, b)

        # Should be very similar
        assert result > 0.95


class TestEuclideanSimilarity:
    """Tests for euclidean similarity function."""

    def test_identical_vectors(self):
        """Test identical vectors have similarity 1.0."""
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([1.0, 0.0, 0.0])

        result = euclidean_similarity(a, b)

        assert result == pytest.approx(1.0)

    def test_distant_vectors_low_similarity(self):
        """Test distant vectors have low similarity."""
        a = np.array([0.0, 0.0, 0.0])
        b = np.array([10.0, 10.0, 10.0])

        result = euclidean_similarity(a, b)

        assert result < 0.1

    def test_close_vectors_high_similarity(self):
        """Test close vectors have high similarity."""
        a = np.array([1.0, 1.0, 1.0])
        b = np.array([1.1, 1.0, 0.9])

        result = euclidean_similarity(a, b)

        assert result > 0.8

    def test_similarity_range(self):
        """Test similarity is always in [0, 1] range."""
        np.random.seed(42)
        for _ in range(100):
            a = np.random.randn(10).astype(np.float32)
            b = np.random.randn(10).astype(np.float32)

            result = euclidean_similarity(a, b)

            assert 0.0 <= result <= 1.0


class TestEmbeddingSimilarityServiceInit:
    """Tests for EmbeddingSimilarityService initialization."""

    def test_init_with_services(self):
        """Test initialization with embedding service and cache."""
        mock_embedding = MagicMock()
        mock_cache = MagicMock()

        service = EmbeddingSimilarityService(
            embedding_service=mock_embedding,
            embedding_cache=mock_cache,
        )

        assert service._embedding_service is mock_embedding
        assert service._embedding_cache is mock_cache

    def test_init_without_cache(self):
        """Test initialization works without cache."""
        mock_embedding = MagicMock()

        service = EmbeddingSimilarityService(
            embedding_service=mock_embedding,
            embedding_cache=None,
        )

        assert service._embedding_cache is None

    def test_init_with_custom_description_length(self):
        """Test initialization with custom max description length."""
        mock_embedding = MagicMock()

        service = EmbeddingSimilarityService(
            embedding_service=mock_embedding,
            max_description_length=200,
        )

        assert service._max_description_length == 200


class TestEntityToText:
    """Tests for entity to text conversion."""

    @pytest.fixture
    def service(self):
        """Create service with mock embedding."""
        mock_embedding = MagicMock()
        return EmbeddingSimilarityService(embedding_service=mock_embedding)

    @pytest.fixture
    def mock_entity(self):
        """Create a mock entity."""
        entity = MagicMock()
        entity.id = uuid4()
        entity.name = "TestEntity"
        entity.entity_type = "CLASS"
        entity.description = "A test entity description"
        return entity

    def test_entity_to_text_includes_name(self, service, mock_entity):
        """Test entity text includes name."""
        text = service.entity_to_text(mock_entity)

        assert mock_entity.name in text

    def test_entity_to_text_includes_type(self, service, mock_entity):
        """Test entity text includes type in brackets."""
        text = service.entity_to_text(mock_entity)

        assert "[CLASS]" in text

    def test_entity_to_text_includes_description(self, service, mock_entity):
        """Test entity text includes description."""
        text = service.entity_to_text(mock_entity)

        assert mock_entity.description in text

    def test_entity_to_text_truncates_long_description(self, service, mock_entity):
        """Test long descriptions are truncated."""
        mock_entity.description = "x" * 1000

        text = service.entity_to_text(mock_entity)

        assert len(text) < 1000
        assert "..." in text

    def test_entity_to_text_handles_no_description(self, service, mock_entity):
        """Test entity without description."""
        mock_entity.description = None

        text = service.entity_to_text(mock_entity)

        assert mock_entity.name in text
        assert "[CLASS]" in text


class TestGetEmbedding:
    """Tests for embedding retrieval."""

    @pytest.fixture
    def mock_embedding_service(self):
        """Create mock embedding service."""
        service = AsyncMock()
        service.encode.return_value = np.random.randn(1024).astype(np.float32)
        return service

    @pytest.fixture
    def mock_cache(self):
        """Create mock embedding cache."""
        cache = AsyncMock()
        cache.get.return_value = None  # Cache miss by default
        cache.set.return_value = True
        return cache

    @pytest.fixture
    def service(self, mock_embedding_service, mock_cache):
        """Create service with mocks."""
        return EmbeddingSimilarityService(
            embedding_service=mock_embedding_service,
            embedding_cache=mock_cache,
        )

    @pytest.fixture
    def mock_entity(self):
        """Create a mock entity."""
        entity = MagicMock()
        entity.id = uuid4()
        entity.name = "TestEntity"
        entity.entity_type = "CLASS"
        entity.description = "A test description"
        return entity

    @pytest.mark.asyncio
    async def test_get_embedding_uses_cache(
        self, service, mock_cache, mock_embedding_service, mock_entity
    ):
        """Test get_embedding checks cache first."""
        cached_embedding = np.random.randn(1024).astype(np.float32)
        mock_cache.get.return_value = cached_embedding
        tenant_id = uuid4()

        result = await service.get_embedding(mock_entity, tenant_id)

        assert np.array_equal(result, cached_embedding)
        mock_cache.get.assert_called_once()
        mock_embedding_service.encode.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_embedding_computes_on_cache_miss(
        self, service, mock_cache, mock_embedding_service, mock_entity
    ):
        """Test get_embedding computes when cache misses."""
        mock_cache.get.return_value = None
        tenant_id = uuid4()

        result = await service.get_embedding(mock_entity, tenant_id)

        mock_embedding_service.encode.assert_called_once()
        mock_cache.set.assert_called_once()
        assert isinstance(result, np.ndarray)

    @pytest.mark.asyncio
    async def test_get_embedding_without_cache(self, mock_embedding_service, mock_entity):
        """Test get_embedding works without cache."""
        service = EmbeddingSimilarityService(
            embedding_service=mock_embedding_service,
            embedding_cache=None,
        )
        tenant_id = uuid4()

        result = await service.get_embedding(mock_entity, tenant_id)

        mock_embedding_service.encode.assert_called_once()
        assert isinstance(result, np.ndarray)

    @pytest.mark.asyncio
    async def test_get_embedding_skip_cache(
        self, service, mock_cache, mock_embedding_service, mock_entity
    ):
        """Test get_embedding can skip cache."""
        tenant_id = uuid4()

        await service.get_embedding(mock_entity, tenant_id, use_cache=False)

        mock_cache.get.assert_not_called()
        mock_embedding_service.encode.assert_called_once()


class TestComputeSimilarity:
    """Tests for similarity computation."""

    @pytest.fixture
    def mock_embedding_service(self):
        """Create mock embedding service."""
        service = AsyncMock()
        return service

    @pytest.fixture
    def service(self, mock_embedding_service):
        """Create service with mock."""
        return EmbeddingSimilarityService(
            embedding_service=mock_embedding_service,
            embedding_cache=None,
        )

    @pytest.fixture
    def mock_entities(self):
        """Create pair of mock entities."""
        entity_a = MagicMock()
        entity_a.id = uuid4()
        entity_a.name = "EntityA"
        entity_a.entity_type = "CLASS"
        entity_a.description = None

        entity_b = MagicMock()
        entity_b.id = uuid4()
        entity_b.name = "EntityB"
        entity_b.entity_type = "CLASS"
        entity_b.description = None

        return entity_a, entity_b

    @pytest.mark.asyncio
    async def test_compute_similarity_identical_embeddings(
        self, service, mock_embedding_service, mock_entities
    ):
        """Test identical embeddings have similarity 1.0."""
        entity_a, entity_b = mock_entities
        identical_embedding = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        mock_embedding_service.encode.return_value = identical_embedding

        result = await service.compute_similarity(entity_a, entity_b, uuid4())

        # Normalized from 1.0 to 1.0 (1.0 + 1) / 2 = 1.0
        assert result == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_compute_similarity_orthogonal_embeddings(
        self, service, mock_embedding_service, mock_entities
    ):
        """Test orthogonal embeddings have similarity 0.5 (normalized)."""
        entity_a, entity_b = mock_entities
        emb_a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        emb_b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        mock_embedding_service.encode.side_effect = [emb_a, emb_b]

        result = await service.compute_similarity(entity_a, entity_b, uuid4())

        # Cosine = 0, normalized = (0 + 1) / 2 = 0.5
        assert result == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_compute_similarity_in_valid_range(
        self, service, mock_embedding_service, mock_entities
    ):
        """Test similarity is always in [0, 1] range."""
        entity_a, entity_b = mock_entities
        emb_a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        emb_b = np.array([-1.0, 0.0, 0.0], dtype=np.float32)
        mock_embedding_service.encode.side_effect = [emb_a, emb_b]

        result = await service.compute_similarity(entity_a, entity_b, uuid4())

        # Cosine = -1, normalized = (-1 + 1) / 2 = 0.0
        assert 0.0 <= result <= 1.0

    @pytest.mark.asyncio
    async def test_compute_similarity_scores_returns_semantic_scores(
        self, service, mock_embedding_service, mock_entities
    ):
        """Test compute_similarity_scores returns SemanticSimilarityScores."""
        entity_a, entity_b = mock_entities
        mock_embedding_service.encode.return_value = np.random.randn(1024).astype(np.float32)

        result = await service.compute_similarity_scores(entity_a, entity_b, uuid4())

        assert result.embedding_cosine is not None
        assert result.embedding_euclidean is not None
        assert result.embedding_cosine.similarity_type == SimilarityType.EMBEDDING_COSINE
        assert result.embedding_euclidean.similarity_type == SimilarityType.EMBEDDING_EUCLIDEAN


class TestBatchSimilarity:
    """Tests for batch similarity computation."""

    @pytest.fixture
    def mock_embedding_service(self):
        """Create mock embedding service."""
        service = AsyncMock()
        return service

    @pytest.fixture
    def mock_cache(self):
        """Create mock embedding cache."""
        cache = AsyncMock()
        return cache

    @pytest.fixture
    def service(self, mock_embedding_service, mock_cache):
        """Create service with mocks."""
        return EmbeddingSimilarityService(
            embedding_service=mock_embedding_service,
            embedding_cache=mock_cache,
        )

    @pytest.fixture
    def mock_entities(self):
        """Create source entity and candidates."""
        source = MagicMock()
        source.id = uuid4()
        source.name = "Source"
        source.entity_type = "CLASS"
        source.description = None

        candidates = []
        for i in range(3):
            c = MagicMock()
            c.id = uuid4()
            c.name = f"Candidate{i}"
            c.entity_type = "CLASS"
            c.description = None
            candidates.append(c)

        return source, candidates

    @pytest.mark.asyncio
    async def test_compute_similarities_batch_empty(self, service):
        """Test batch with empty candidates returns empty list."""
        source = MagicMock()
        source.id = uuid4()

        result = await service.compute_similarities_batch(source, [], uuid4())

        assert result == []

    @pytest.mark.asyncio
    async def test_compute_similarities_batch_uses_cache(
        self, service, mock_cache, mock_embedding_service, mock_entities
    ):
        """Test batch uses cache for candidates."""
        source, candidates = mock_entities
        tenant_id = uuid4()

        # Source embedding
        source_emb = np.random.randn(1024).astype(np.float32)
        mock_embedding_service.encode.return_value = source_emb

        # Cache returns embeddings for all candidates
        cached = {c.id: np.random.randn(1024).astype(np.float32) for c in candidates}
        mock_cache.get.return_value = None  # Source not cached
        mock_cache.get_batch.return_value = cached

        result = await service.compute_similarities_batch(source, candidates, tenant_id)

        assert len(result) == 3
        mock_cache.get_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_compute_similarities_batch_sorted_descending(
        self, service, mock_cache, mock_embedding_service, mock_entities
    ):
        """Test batch results are sorted by similarity descending."""
        source, candidates = mock_entities
        tenant_id = uuid4()

        source_emb = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        mock_embedding_service.encode.return_value = source_emb

        # Different similarities
        cached = {
            candidates[0].id: np.array([0.5, 0.5, 0.0], dtype=np.float32),  # Lower
            candidates[1].id: np.array([1.0, 0.0, 0.0], dtype=np.float32),  # Highest
            candidates[2].id: np.array([0.7, 0.3, 0.0], dtype=np.float32),  # Medium
        }
        mock_cache.get.return_value = None
        mock_cache.get_batch.return_value = cached

        result = await service.compute_similarities_batch(source, candidates, tenant_id)

        # Should be sorted by similarity descending
        similarities = [sim for _, sim in result]
        assert similarities == sorted(similarities, reverse=True)


class TestInvalidateEntityEmbedding:
    """Tests for embedding cache invalidation."""

    @pytest.mark.asyncio
    async def test_invalidate_calls_cache(self):
        """Test invalidate calls cache invalidate."""
        mock_cache = AsyncMock()
        service = EmbeddingSimilarityService(
            embedding_service=MagicMock(),
            embedding_cache=mock_cache,
        )

        entity_id = uuid4()
        tenant_id = uuid4()

        await service.invalidate_entity_embedding(entity_id, tenant_id)

        mock_cache.invalidate.assert_called_once_with(tenant_id, entity_id)

    @pytest.mark.asyncio
    async def test_invalidate_without_cache(self):
        """Test invalidate works without cache."""
        service = EmbeddingSimilarityService(
            embedding_service=MagicMock(),
            embedding_cache=None,
        )

        # Should not raise
        await service.invalidate_entity_embedding(uuid4(), uuid4())


class TestGetEmbeddingSimilarityService:
    """Tests for factory function."""

    @pytest.mark.asyncio
    async def test_returns_service(self):
        """Test factory returns configured service."""
        mock_embedding_service = MagicMock()
        mock_cache = MagicMock()

        with patch("kg_builder.services.embedding.get_embedding_service") as mock_get_emb:
            mock_get_emb.return_value = mock_embedding_service

            with patch("kg_builder.services.embedding_cache.get_embedding_cache") as mock_get_cache:
                mock_get_cache.return_value = mock_cache

                result = await get_embedding_similarity_service()

                assert result is not None
                assert isinstance(result, EmbeddingSimilarityService)

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        """Test factory returns None on error."""
        with patch("kg_builder.services.embedding.get_embedding_service") as mock_get_emb:
            mock_get_emb.side_effect = Exception("Error")

            result = await get_embedding_similarity_service()

            assert result is None
