"""
Unit tests for CombinedScoringPipeline.

Tests the combined similarity scoring with mocked services.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from kg_builder.schemas.similarity import (
    ContextualSignals,
    SimilarityScore,
    SimilarityScores,
    SimilarityType,
    StringSimilarityScores,
)
from kg_builder.services.consolidation.combined_scoring import (
    CombinedScoringPipeline,
    FeatureWeights,
    ScoringResult,
    create_default_config_for_scoring,
)


class TestFeatureWeights:
    """Tests for FeatureWeights dataclass."""

    def test_default_weights(self):
        """Test default weight values."""
        weights = FeatureWeights.default()

        assert weights.jaro_winkler == 0.15
        assert weights.normalized_exact == 0.20
        assert weights.type_match == 0.10
        assert weights.embedding_cosine == 0.35
        assert weights.graph_neighborhood == 0.20

    def test_from_config(self):
        """Test creating weights from config."""
        mock_config = MagicMock()
        mock_config.feature_weights = {
            "jaro_winkler": 0.25,
            "normalized_exact": 0.30,
            "type_match": 0.15,
            "embedding_cosine": 0.20,
            "graph_neighborhood": 0.10,
        }

        weights = FeatureWeights.from_config(mock_config)

        assert weights.jaro_winkler == 0.25
        assert weights.normalized_exact == 0.30
        assert weights.type_match == 0.15
        assert weights.embedding_cosine == 0.20
        assert weights.graph_neighborhood == 0.10

    def test_from_config_uses_defaults_for_missing(self):
        """Test defaults are used for missing weights."""
        mock_config = MagicMock()
        mock_config.feature_weights = {}

        weights = FeatureWeights.from_config(mock_config)

        assert weights.jaro_winkler == 0.15

    def test_normalize_sums_to_one(self):
        """Test normalized weights sum to 1.0."""
        weights = FeatureWeights.default()
        enabled = {"jaro_winkler", "normalized_exact", "type_match"}

        normalized = weights.normalize(enabled)

        assert sum(normalized.values()) == pytest.approx(1.0)

    def test_normalize_excludes_disabled(self):
        """Test disabled features are excluded."""
        weights = FeatureWeights.default()
        enabled = {"jaro_winkler", "type_match"}

        normalized = weights.normalize(enabled)

        assert "embedding_cosine" not in normalized
        assert "graph_neighborhood" not in normalized
        assert "normalized_exact" not in normalized

    def test_normalize_empty_enabled(self):
        """Test normalize with no enabled features."""
        weights = FeatureWeights.default()

        normalized = weights.normalize(set())

        assert normalized == {}

    def test_normalize_all_zero_weights(self):
        """Test normalize handles all-zero weights."""
        weights = FeatureWeights(
            jaro_winkler=0.0,
            normalized_exact=0.0,
            type_match=0.0,
        )
        enabled = {"jaro_winkler", "normalized_exact", "type_match"}

        normalized = weights.normalize(enabled)

        # Should get equal weights
        assert all(v == pytest.approx(1 / 3) for v in normalized.values())

    def test_to_dict(self):
        """Test conversion to dictionary."""
        weights = FeatureWeights.default()

        result = weights.to_dict()

        assert isinstance(result, dict)
        assert "jaro_winkler" in result
        assert "embedding_cosine" in result


class TestScoringResult:
    """Tests for ScoringResult dataclass."""

    def test_init_defaults(self):
        """Test default values."""
        result = ScoringResult(
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
        )

        assert result.combined_score == 0.0
        assert result.classification == "low"
        assert result.weights_used == {}

    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = ScoringResult(
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            jaro_winkler=0.85,
            embedding_cosine=0.90,
            combined_score=0.88,
            classification="medium",
        )

        d = result.to_dict()

        assert "entity_a_id" in d
        assert "entity_b_id" in d
        assert d["scores"]["jaro_winkler"] == 0.85
        assert d["scores"]["embedding_cosine"] == 0.90
        assert d["combined_score"] == 0.88
        assert d["classification"] == "medium"

    def test_get_decision_high(self):
        """Test high classification maps to auto_merge."""
        result = ScoringResult(
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            classification="high",
        )

        assert result.get_decision() == "auto_merge"

    def test_get_decision_medium(self):
        """Test medium classification maps to review."""
        result = ScoringResult(
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            classification="medium",
        )

        assert result.get_decision() == "review"

    def test_get_decision_low(self):
        """Test low classification maps to reject."""
        result = ScoringResult(
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            classification="low",
        )

        assert result.get_decision() == "reject"


class TestCombinedScoringPipelineInit:
    """Tests for CombinedScoringPipeline initialization."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = MagicMock()
        config.auto_merge_threshold = 0.90
        config.review_threshold = 0.50
        config.enable_embedding_similarity = True
        config.enable_graph_similarity = True
        config.feature_weights = {}
        return config

    def test_init_with_services(self, mock_config):
        """Test initialization with all services."""
        mock_embedding = MagicMock()
        mock_graph = MagicMock()

        pipeline = CombinedScoringPipeline(
            embedding_similarity=mock_embedding,
            graph_similarity=mock_graph,
            config=mock_config,
        )

        assert pipeline._embedding_similarity is mock_embedding
        assert pipeline._graph_similarity is mock_graph
        assert pipeline._config is mock_config

    def test_init_without_optional_services(self, mock_config):
        """Test initialization without optional services."""
        pipeline = CombinedScoringPipeline(
            embedding_similarity=None,
            graph_similarity=None,
            config=mock_config,
        )

        assert pipeline._embedding_similarity is None
        assert pipeline._graph_similarity is None


class TestClassifyConfidence:
    """Tests for confidence classification."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = MagicMock()
        config.auto_merge_threshold = 0.90
        config.review_threshold = 0.50
        config.enable_embedding_similarity = False
        config.enable_graph_similarity = False
        config.feature_weights = {}
        return config

    @pytest.fixture
    def pipeline(self, mock_config):
        """Create pipeline with mock config."""
        return CombinedScoringPipeline(
            embedding_similarity=None,
            graph_similarity=None,
            config=mock_config,
        )

    def test_classify_high(self, pipeline):
        """Test high confidence classification."""
        assert pipeline._classify_confidence(0.95) == "high"
        assert pipeline._classify_confidence(0.90) == "high"

    def test_classify_medium(self, pipeline):
        """Test medium confidence classification."""
        assert pipeline._classify_confidence(0.75) == "medium"
        assert pipeline._classify_confidence(0.50) == "medium"

    def test_classify_low(self, pipeline):
        """Test low confidence classification."""
        assert pipeline._classify_confidence(0.30) == "low"
        assert pipeline._classify_confidence(0.49) == "low"


class TestComputeCombinedScore:
    """Tests for combined score computation."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = MagicMock()
        config.auto_merge_threshold = 0.90
        config.review_threshold = 0.50
        config.enable_embedding_similarity = True
        config.enable_graph_similarity = True
        config.feature_weights = {
            "jaro_winkler": 0.2,
            "normalized_exact": 0.2,
            "type_match": 0.1,
            "embedding_cosine": 0.3,
            "graph_neighborhood": 0.2,
        }
        return config

    @pytest.fixture
    def mock_entities(self):
        """Create mock entities."""
        entity_a = MagicMock()
        entity_a.id = uuid4()
        entity_a.name = "EntityA"

        entity_b = MagicMock()
        entity_b.id = uuid4()
        entity_b.name = "EntityB"

        return entity_a, entity_b

    @pytest.fixture
    def mock_string_scores(self, mock_entities):
        """Create mock string scores."""
        entity_a, entity_b = mock_entities
        scores = SimilarityScores(
            entity_a_id=entity_a.id,
            entity_b_id=entity_b.id,
        )
        scores.string_scores = StringSimilarityScores(
            jaro_winkler=SimilarityScore(
                similarity_type=SimilarityType.JARO_WINKLER,
                raw_score=0.85,
            ),
            normalized_exact=SimilarityScore(
                similarity_type=SimilarityType.NORMALIZED_EXACT,
                raw_score=0.0,
            ),
        )
        scores.contextual = ContextualSignals(
            type_match=SimilarityScore(
                similarity_type=SimilarityType.TYPE_MATCH,
                raw_score=1.0,
            ),
        )
        return scores

    @pytest.mark.asyncio
    async def test_combined_score_string_only(self, mock_config, mock_entities, mock_string_scores):
        """Test combined score with only string scores."""
        mock_config.enable_embedding_similarity = False
        mock_config.enable_graph_similarity = False

        pipeline = CombinedScoringPipeline(
            embedding_similarity=None,
            graph_similarity=None,
            config=mock_config,
        )

        entity_a, entity_b = mock_entities
        tenant_id = uuid4()

        result = await pipeline.compute_combined_score(
            entity_a, entity_b, mock_string_scores, tenant_id
        )

        assert isinstance(result, ScoringResult)
        assert result.jaro_winkler == 0.85
        assert result.type_match == 1.0
        assert result.embedding_cosine is None
        assert result.graph_neighborhood is None
        assert 0.0 <= result.combined_score <= 1.0

    @pytest.mark.asyncio
    async def test_combined_score_with_embedding(
        self, mock_config, mock_entities, mock_string_scores
    ):
        """Test combined score includes embedding similarity."""
        mock_embedding = AsyncMock()
        mock_embedding.compute_similarity.return_value = 0.90

        mock_config.enable_graph_similarity = False

        pipeline = CombinedScoringPipeline(
            embedding_similarity=mock_embedding,
            graph_similarity=None,
            config=mock_config,
        )

        entity_a, entity_b = mock_entities
        tenant_id = uuid4()

        result = await pipeline.compute_combined_score(
            entity_a, entity_b, mock_string_scores, tenant_id
        )

        assert result.embedding_cosine == 0.90
        mock_embedding.compute_similarity.assert_called_once()

    @pytest.mark.asyncio
    async def test_combined_score_with_graph(self, mock_config, mock_entities, mock_string_scores):
        """Test combined score includes graph similarity."""
        mock_graph = AsyncMock()
        mock_graph.compute_similarity.return_value = 0.75

        mock_config.enable_embedding_similarity = False

        pipeline = CombinedScoringPipeline(
            embedding_similarity=None,
            graph_similarity=mock_graph,
            config=mock_config,
        )

        entity_a, entity_b = mock_entities
        tenant_id = uuid4()

        result = await pipeline.compute_combined_score(
            entity_a, entity_b, mock_string_scores, tenant_id
        )

        assert result.graph_neighborhood == 0.75
        mock_graph.compute_similarity.assert_called_once()

    @pytest.mark.asyncio
    async def test_combined_score_handles_embedding_error(
        self, mock_config, mock_entities, mock_string_scores
    ):
        """Test combined score handles embedding service errors."""
        mock_embedding = AsyncMock()
        mock_embedding.compute_similarity.side_effect = Exception("Service error")

        pipeline = CombinedScoringPipeline(
            embedding_similarity=mock_embedding,
            graph_similarity=None,
            config=mock_config,
        )

        entity_a, entity_b = mock_entities
        tenant_id = uuid4()

        # Should not raise, just skip embedding score
        result = await pipeline.compute_combined_score(
            entity_a, entity_b, mock_string_scores, tenant_id
        )

        assert result.embedding_cosine is None

    @pytest.mark.asyncio
    async def test_combined_score_range(self, mock_config, mock_entities, mock_string_scores):
        """Test combined score is always in valid range."""
        mock_embedding = AsyncMock()
        mock_embedding.compute_similarity.return_value = 0.95

        mock_graph = AsyncMock()
        mock_graph.compute_similarity.return_value = 0.80

        pipeline = CombinedScoringPipeline(
            embedding_similarity=mock_embedding,
            graph_similarity=mock_graph,
            config=mock_config,
        )

        entity_a, entity_b = mock_entities
        tenant_id = uuid4()

        result = await pipeline.compute_combined_score(
            entity_a, entity_b, mock_string_scores, tenant_id
        )

        assert 0.0 <= result.combined_score <= 1.0


class TestBatchScoring:
    """Tests for batch score computation."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = MagicMock()
        config.auto_merge_threshold = 0.90
        config.review_threshold = 0.50
        config.enable_embedding_similarity = True
        config.enable_graph_similarity = True
        config.feature_weights = {}
        return config

    @pytest.fixture
    def mock_source_and_candidates(self):
        """Create source entity and candidates with scores."""
        source = MagicMock()
        source.id = uuid4()
        source.name = "Source"

        candidates = []
        for i in range(3):
            c = MagicMock()
            c.id = uuid4()
            c.name = f"Candidate{i}"

            scores = SimilarityScores(
                entity_a_id=source.id,
                entity_b_id=c.id,
            )
            scores.string_scores = StringSimilarityScores(
                jaro_winkler=SimilarityScore(
                    similarity_type=SimilarityType.JARO_WINKLER,
                    raw_score=0.8 + i * 0.05,
                ),
            )
            scores.contextual = ContextualSignals()

            candidates.append((c, scores))

        return source, candidates

    @pytest.mark.asyncio
    async def test_batch_scores_empty(self, mock_config):
        """Test batch with empty candidates."""
        pipeline = CombinedScoringPipeline(
            embedding_similarity=None,
            graph_similarity=None,
            config=mock_config,
        )

        source = MagicMock()
        source.id = uuid4()

        result = await pipeline.compute_batch_scores(source, [], uuid4())

        assert result == []

    @pytest.mark.asyncio
    async def test_batch_scores_sorted_descending(self, mock_config, mock_source_and_candidates):
        """Test batch results are sorted by combined score descending."""
        mock_config.enable_embedding_similarity = False
        mock_config.enable_graph_similarity = False

        pipeline = CombinedScoringPipeline(
            embedding_similarity=None,
            graph_similarity=None,
            config=mock_config,
        )

        source, candidates = mock_source_and_candidates
        tenant_id = uuid4()

        results = await pipeline.compute_batch_scores(source, candidates, tenant_id)

        scores = [r.combined_score for r in results]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_batch_scores_uses_batch_embedding(self, mock_config, mock_source_and_candidates):
        """Test batch uses batch embedding computation."""
        mock_embedding = AsyncMock()
        mock_embedding.compute_similarities_batch.return_value = [
            (mock_source_and_candidates[1][0][0], 0.85),
            (mock_source_and_candidates[1][1][0], 0.80),
            (mock_source_and_candidates[1][2][0], 0.75),
        ]

        mock_config.enable_graph_similarity = False

        pipeline = CombinedScoringPipeline(
            embedding_similarity=mock_embedding,
            graph_similarity=None,
            config=mock_config,
        )

        source, candidates = mock_source_and_candidates
        tenant_id = uuid4()

        await pipeline.compute_batch_scores(source, candidates, tenant_id)

        mock_embedding.compute_similarities_batch.assert_called_once()


class TestRouteDecision:
    """Tests for decision routing."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = MagicMock()
        config.auto_merge_threshold = 0.90
        config.review_threshold = 0.50
        config.enable_embedding_similarity = False
        config.enable_graph_similarity = False
        config.feature_weights = {}
        return config

    @pytest.fixture
    def pipeline(self, mock_config):
        """Create pipeline."""
        return CombinedScoringPipeline(
            embedding_similarity=None,
            graph_similarity=None,
            config=mock_config,
        )

    def test_route_auto_merge(self, pipeline):
        """Test high confidence routes to auto_merge."""
        result = ScoringResult(
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            classification="high",
        )

        decision = pipeline.route_decision(result)

        assert decision == "auto_merge"

    def test_route_review(self, pipeline):
        """Test medium confidence routes to review."""
        result = ScoringResult(
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            classification="medium",
        )

        decision = pipeline.route_decision(result)

        assert decision == "review"

    def test_route_reject(self, pipeline):
        """Test low confidence routes to reject."""
        result = ScoringResult(
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            classification="low",
        )

        decision = pipeline.route_decision(result)

        assert decision == "reject"


class TestProcessCandidatePair:
    """Tests for process_candidate_pair convenience method."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = MagicMock()
        config.auto_merge_threshold = 0.90
        config.review_threshold = 0.50
        config.enable_embedding_similarity = False
        config.enable_graph_similarity = False
        config.feature_weights = {}
        return config

    @pytest.mark.asyncio
    async def test_returns_result_and_decision(self, mock_config):
        """Test returns both result and decision."""
        pipeline = CombinedScoringPipeline(
            embedding_similarity=None,
            graph_similarity=None,
            config=mock_config,
        )

        entity_a = MagicMock()
        entity_a.id = uuid4()
        entity_b = MagicMock()
        entity_b.id = uuid4()

        scores = SimilarityScores(
            entity_a_id=entity_a.id,
            entity_b_id=entity_b.id,
        )
        scores.string_scores = StringSimilarityScores(
            jaro_winkler=SimilarityScore(
                similarity_type=SimilarityType.JARO_WINKLER,
                raw_score=0.95,
            ),
        )
        scores.contextual = ContextualSignals()

        result, decision = await pipeline.process_candidate_pair(
            entity_a, entity_b, scores, uuid4()
        )

        assert isinstance(result, ScoringResult)
        assert decision in ["auto_merge", "review", "reject"]


class TestCreateDefaultConfigForScoring:
    """Tests for default config factory."""

    def test_returns_dict_with_defaults(self):
        """Test returns dict with default values."""
        config = create_default_config_for_scoring()

        assert isinstance(config, dict)
        assert config["auto_merge_threshold"] == 0.90
        assert config["review_threshold"] == 0.50
        assert config["enable_embedding_similarity"] is True
        assert config["enable_graph_similarity"] is True
        assert "feature_weights" in config
