"""
Unit tests for similarity scoring schemas.

These tests verify the Pydantic models used for representing similarity
scores between entity pairs during entity consolidation.
"""

import pytest
from uuid import uuid4

from kg_builder.schemas.similarity import (
    SimilarityType,
    SimilarityScore,
    StringSimilarityScores,
    PhoneticSimilarityScores,
    SemanticSimilarityScores,
    GraphSimilarityScores,
    ContextualSignals,
    SimilarityScores,
    WeightConfiguration,
    ScoreComputation,
    SimilarityThresholds,
)


class TestSimilarityType:
    """Tests for SimilarityType enum."""

    def test_all_similarity_types_defined(self):
        """Test that all expected similarity types are defined."""
        # String types
        assert SimilarityType.JARO_WINKLER == "jaro_winkler"
        assert SimilarityType.LEVENSHTEIN == "levenshtein"
        assert SimilarityType.DAMERAU_LEVENSHTEIN == "damerau_levenshtein"
        assert SimilarityType.NORMALIZED_EXACT == "normalized_exact"
        assert SimilarityType.TRIGRAM == "trigram"

        # Phonetic types
        assert SimilarityType.SOUNDEX == "soundex"
        assert SimilarityType.METAPHONE == "metaphone"
        assert SimilarityType.NYSIIS == "nysiis"

        # Semantic types
        assert SimilarityType.EMBEDDING_COSINE == "embedding_cosine"
        assert SimilarityType.EMBEDDING_EUCLIDEAN == "embedding_euclidean"

        # Graph types
        assert SimilarityType.GRAPH_NEIGHBORHOOD == "graph_neighborhood"
        assert SimilarityType.GRAPH_CO_OCCURRENCE == "graph_co_occurrence"

        # Contextual types
        assert SimilarityType.SAME_PAGE == "same_page"
        assert SimilarityType.TYPE_MATCH == "type_match"
        assert SimilarityType.PROPERTY_OVERLAP == "property_overlap"

        # Composite types
        assert SimilarityType.FAST_COMPOSITE == "fast_composite"
        assert SimilarityType.FULL_COMPOSITE == "full_composite"


class TestSimilarityScore:
    """Tests for individual SimilarityScore model."""

    def test_create_basic_score(self):
        """Test creating a basic similarity score."""
        score = SimilarityScore(
            similarity_type=SimilarityType.JARO_WINKLER,
            raw_score=0.85,
        )

        assert score.similarity_type == SimilarityType.JARO_WINKLER
        assert score.raw_score == 0.85
        assert score.weight == 1.0  # Default
        assert score.is_computed is True  # Default

    def test_score_with_weight(self):
        """Test creating a score with custom weight."""
        score = SimilarityScore(
            similarity_type=SimilarityType.EMBEDDING_COSINE,
            raw_score=0.9,
            weight=0.5,
        )

        assert score.raw_score == 0.9
        assert score.weight == 0.5
        assert score.weighted_score == 0.45

    def test_score_range_validation(self):
        """Test that scores must be in valid range."""
        # Raw score must be 0.0-1.0
        with pytest.raises(ValueError):
            SimilarityScore(
                similarity_type=SimilarityType.JARO_WINKLER,
                raw_score=1.5,
            )

        with pytest.raises(ValueError):
            SimilarityScore(
                similarity_type=SimilarityType.JARO_WINKLER,
                raw_score=-0.1,
            )

    def test_weight_range_validation(self):
        """Test that weights must be in valid range."""
        with pytest.raises(ValueError):
            SimilarityScore(
                similarity_type=SimilarityType.JARO_WINKLER,
                raw_score=0.5,
                weight=1.5,
            )

    def test_computation_time(self):
        """Test computation time tracking."""
        score = SimilarityScore(
            similarity_type=SimilarityType.EMBEDDING_COSINE,
            raw_score=0.95,
            computation_time_ms=12.5,
        )

        assert score.computation_time_ms == 12.5

    def test_is_computed_flag(self):
        """Test is_computed flag for skipped scores."""
        score = SimilarityScore(
            similarity_type=SimilarityType.EMBEDDING_COSINE,
            raw_score=0.0,
            is_computed=False,
        )

        assert score.is_computed is False


class TestStringSimilarityScores:
    """Tests for StringSimilarityScores collection."""

    def test_empty_string_scores(self):
        """Test empty string scores collection."""
        scores = StringSimilarityScores()

        assert scores.jaro_winkler is None
        assert scores.levenshtein is None
        assert scores.damerau_levenshtein is None
        assert scores.normalized_exact is None
        assert scores.trigram is None

    def test_get_best_score(self):
        """Test getting the best string similarity score."""
        scores = StringSimilarityScores(
            jaro_winkler=SimilarityScore(
                similarity_type=SimilarityType.JARO_WINKLER,
                raw_score=0.8,
            ),
            levenshtein=SimilarityScore(
                similarity_type=SimilarityType.LEVENSHTEIN,
                raw_score=0.75,
            ),
            normalized_exact=SimilarityScore(
                similarity_type=SimilarityType.NORMALIZED_EXACT,
                raw_score=0.95,
            ),
        )

        best = scores.get_best_score()
        assert best is not None
        assert best.raw_score == 0.95
        assert best.similarity_type == SimilarityType.NORMALIZED_EXACT

    def test_get_best_score_empty(self):
        """Test getting best score from empty collection."""
        scores = StringSimilarityScores()
        assert scores.get_best_score() is None

    def test_get_average_score(self):
        """Test getting average of string scores."""
        scores = StringSimilarityScores(
            jaro_winkler=SimilarityScore(
                similarity_type=SimilarityType.JARO_WINKLER,
                raw_score=0.8,
            ),
            levenshtein=SimilarityScore(
                similarity_type=SimilarityType.LEVENSHTEIN,
                raw_score=0.6,
            ),
        )

        avg = scores.get_average_score()
        assert avg == 0.7  # (0.8 + 0.6) / 2

    def test_get_average_score_empty(self):
        """Test average score from empty collection."""
        scores = StringSimilarityScores()
        assert scores.get_average_score() == 0.0


class TestPhoneticSimilarityScores:
    """Tests for PhoneticSimilarityScores collection."""

    def test_any_match_soundex(self):
        """Test any_match with soundex match."""
        scores = PhoneticSimilarityScores(
            soundex=SimilarityScore(
                similarity_type=SimilarityType.SOUNDEX,
                raw_score=1.0,
            ),
        )

        assert scores.any_match() is True

    def test_any_match_partial(self):
        """Test any_match with partial match."""
        scores = PhoneticSimilarityScores(
            soundex=SimilarityScore(
                similarity_type=SimilarityType.SOUNDEX,
                raw_score=0.5,  # Not a full match
            ),
        )

        assert scores.any_match() is False

    def test_any_match_empty(self):
        """Test any_match with no scores."""
        scores = PhoneticSimilarityScores()
        assert scores.any_match() is False


class TestSemanticSimilarityScores:
    """Tests for SemanticSimilarityScores collection."""

    def test_has_embedding_score_true(self):
        """Test has_embedding_score when cosine is computed."""
        scores = SemanticSimilarityScores(
            embedding_cosine=SimilarityScore(
                similarity_type=SimilarityType.EMBEDDING_COSINE,
                raw_score=0.92,
            ),
        )

        assert scores.has_embedding_score() is True

    def test_has_embedding_score_false(self):
        """Test has_embedding_score when nothing computed."""
        scores = SemanticSimilarityScores()
        assert scores.has_embedding_score() is False

    def test_get_primary_score_cosine(self):
        """Test get_primary_score prefers cosine."""
        scores = SemanticSimilarityScores(
            embedding_cosine=SimilarityScore(
                similarity_type=SimilarityType.EMBEDDING_COSINE,
                raw_score=0.92,
            ),
            embedding_euclidean=SimilarityScore(
                similarity_type=SimilarityType.EMBEDDING_EUCLIDEAN,
                raw_score=0.88,
            ),
        )

        primary = scores.get_primary_score()
        assert primary is not None
        assert primary.similarity_type == SimilarityType.EMBEDDING_COSINE

    def test_get_primary_score_fallback(self):
        """Test get_primary_score falls back to euclidean."""
        scores = SemanticSimilarityScores(
            embedding_euclidean=SimilarityScore(
                similarity_type=SimilarityType.EMBEDDING_EUCLIDEAN,
                raw_score=0.88,
            ),
        )

        primary = scores.get_primary_score()
        assert primary is not None
        assert primary.similarity_type == SimilarityType.EMBEDDING_EUCLIDEAN


class TestGraphSimilarityScores:
    """Tests for GraphSimilarityScores collection."""

    def test_has_graph_score(self):
        """Test has_graph_score detection."""
        scores = GraphSimilarityScores(
            neighborhood=SimilarityScore(
                similarity_type=SimilarityType.GRAPH_NEIGHBORHOOD,
                raw_score=0.7,
            ),
        )

        assert scores.has_graph_score() is True

    def test_has_graph_score_empty(self):
        """Test has_graph_score when empty."""
        scores = GraphSimilarityScores()
        assert scores.has_graph_score() is False


class TestContextualSignals:
    """Tests for ContextualSignals collection."""

    def test_are_same_type_true(self):
        """Test are_same_type property when types match."""
        signals = ContextualSignals(
            type_match=SimilarityScore(
                similarity_type=SimilarityType.TYPE_MATCH,
                raw_score=1.0,
            ),
        )

        assert signals.are_same_type is True

    def test_are_same_type_false(self):
        """Test are_same_type property when types differ."""
        signals = ContextualSignals(
            type_match=SimilarityScore(
                similarity_type=SimilarityType.TYPE_MATCH,
                raw_score=0.0,
            ),
        )

        assert signals.are_same_type is False

    def test_are_from_same_page_true(self):
        """Test are_from_same_page property."""
        signals = ContextualSignals(
            same_page=SimilarityScore(
                similarity_type=SimilarityType.SAME_PAGE,
                raw_score=1.0,
            ),
        )

        assert signals.are_from_same_page is True


class TestSimilarityScores:
    """Tests for the main SimilarityScores model."""

    @pytest.fixture
    def entity_ids(self):
        """Generate entity IDs for tests."""
        return uuid4(), uuid4()

    def test_create_empty_scores(self, entity_ids):
        """Test creating empty similarity scores."""
        entity_a_id, entity_b_id = entity_ids

        scores = SimilarityScores(
            entity_a_id=entity_a_id,
            entity_b_id=entity_b_id,
        )

        assert scores.entity_a_id == entity_a_id
        assert scores.entity_b_id == entity_b_id
        assert scores.combined_score == 0.0
        assert scores.confidence == 0.0

    def test_create_populated_scores(self, entity_ids):
        """Test creating fully populated similarity scores."""
        entity_a_id, entity_b_id = entity_ids

        scores = SimilarityScores(
            entity_a_id=entity_a_id,
            entity_b_id=entity_b_id,
            string_scores=StringSimilarityScores(
                jaro_winkler=SimilarityScore(
                    similarity_type=SimilarityType.JARO_WINKLER,
                    raw_score=0.85,
                ),
            ),
            semantic_scores=SemanticSimilarityScores(
                embedding_cosine=SimilarityScore(
                    similarity_type=SimilarityType.EMBEDDING_COSINE,
                    raw_score=0.92,
                ),
            ),
            combined_score=0.88,
            confidence=0.90,
        )

        assert scores.string_scores.jaro_winkler.raw_score == 0.85
        assert scores.semantic_scores.embedding_cosine.raw_score == 0.92
        assert scores.combined_score == 0.88
        assert scores.confidence == 0.90

    def test_to_dict_serialization(self, entity_ids):
        """Test serialization to flat dictionary."""
        entity_a_id, entity_b_id = entity_ids

        scores = SimilarityScores(
            entity_a_id=entity_a_id,
            entity_b_id=entity_b_id,
            string_scores=StringSimilarityScores(
                jaro_winkler=SimilarityScore(
                    similarity_type=SimilarityType.JARO_WINKLER,
                    raw_score=0.85,
                ),
                normalized_exact=SimilarityScore(
                    similarity_type=SimilarityType.NORMALIZED_EXACT,
                    raw_score=1.0,
                ),
            ),
            phonetic_scores=PhoneticSimilarityScores(
                soundex=SimilarityScore(
                    similarity_type=SimilarityType.SOUNDEX,
                    raw_score=1.0,
                ),
            ),
            combined_score=0.92,
            confidence=0.95,
        )

        data = scores.to_dict()

        assert data["jaro_winkler"] == 0.85
        assert data["normalized_exact"] == 1.0
        assert data["soundex"] == 1.0
        assert data["combined_score"] == 0.92
        assert data["confidence"] == 0.95

    def test_from_dict_deserialization(self, entity_ids):
        """Test reconstruction from flat dictionary."""
        entity_a_id, entity_b_id = entity_ids

        data = {
            "jaro_winkler": 0.85,
            "normalized_exact": 1.0,
            "soundex": 1.0,
            "embedding_cosine": 0.92,
            "combined_score": 0.90,
            "confidence": 0.88,
            "uncertainty": 0.05,
        }

        scores = SimilarityScores.from_dict(entity_a_id, entity_b_id, data)

        assert scores.entity_a_id == entity_a_id
        assert scores.string_scores.jaro_winkler.raw_score == 0.85
        assert scores.string_scores.normalized_exact.raw_score == 1.0
        assert scores.phonetic_scores.soundex.raw_score == 1.0
        assert scores.semantic_scores.embedding_cosine.raw_score == 0.92
        assert scores.combined_score == 0.90
        assert scores.confidence == 0.88
        assert scores.uncertainty == 0.05

    def test_get_all_computed_scores(self, entity_ids):
        """Test getting all computed scores as flat list."""
        entity_a_id, entity_b_id = entity_ids

        scores = SimilarityScores(
            entity_a_id=entity_a_id,
            entity_b_id=entity_b_id,
            string_scores=StringSimilarityScores(
                jaro_winkler=SimilarityScore(
                    similarity_type=SimilarityType.JARO_WINKLER,
                    raw_score=0.85,
                ),
            ),
            phonetic_scores=PhoneticSimilarityScores(
                soundex=SimilarityScore(
                    similarity_type=SimilarityType.SOUNDEX,
                    raw_score=1.0,
                ),
            ),
        )

        all_scores = scores.get_all_computed_scores()
        assert len(all_scores) == 2

    def test_score_count(self, entity_ids):
        """Test score_count property."""
        entity_a_id, entity_b_id = entity_ids

        scores = SimilarityScores(
            entity_a_id=entity_a_id,
            entity_b_id=entity_b_id,
            string_scores=StringSimilarityScores(
                jaro_winkler=SimilarityScore(
                    similarity_type=SimilarityType.JARO_WINKLER,
                    raw_score=0.85,
                ),
                levenshtein=SimilarityScore(
                    similarity_type=SimilarityType.LEVENSHTEIN,
                    raw_score=0.80,
                ),
            ),
        )

        assert scores.score_count == 2

    def test_confidence_levels(self, entity_ids):
        """Test confidence level properties."""
        entity_a_id, entity_b_id = entity_ids

        # High confidence
        high_conf = SimilarityScores(
            entity_a_id=entity_a_id,
            entity_b_id=entity_b_id,
            confidence=0.95,
        )
        assert high_conf.is_high_confidence is True
        assert high_conf.is_medium_confidence is False
        assert high_conf.is_low_confidence is False

        # Medium confidence
        med_conf = SimilarityScores(
            entity_a_id=entity_a_id,
            entity_b_id=entity_b_id,
            confidence=0.7,
        )
        assert med_conf.is_high_confidence is False
        assert med_conf.is_medium_confidence is True
        assert med_conf.is_low_confidence is False

        # Low confidence
        low_conf = SimilarityScores(
            entity_a_id=entity_a_id,
            entity_b_id=entity_b_id,
            confidence=0.3,
        )
        assert low_conf.is_high_confidence is False
        assert low_conf.is_medium_confidence is False
        assert low_conf.is_low_confidence is True


class TestWeightConfiguration:
    """Tests for WeightConfiguration model."""

    def test_default_weights(self):
        """Test default weight values."""
        config = WeightConfiguration.default()

        assert config.jaro_winkler == 0.3
        assert config.normalized_exact == 0.4
        assert config.embedding_cosine == 0.5
        assert config.graph_neighborhood == 0.3
        assert config.same_page_bonus == 0.1
        assert config.type_match_bonus == 0.2

    def test_person_weights(self):
        """Test person-optimized weight configuration."""
        config = WeightConfiguration.for_person_entities()

        # Person names benefit from Jaro-Winkler
        assert config.jaro_winkler == 0.4
        # Phonetic important for names
        assert config.soundex == 0.2

    def test_organization_weights(self):
        """Test organization-optimized weight configuration."""
        config = WeightConfiguration.for_organization_entities()

        # Org names often have exact matches
        assert config.normalized_exact == 0.5
        # Trigram helps with abbreviations
        assert config.trigram == 0.3

    def test_technical_weights(self):
        """Test technical entity weight configuration."""
        config = WeightConfiguration.for_technical_entities()

        # Technical names are often exact
        assert config.normalized_exact == 0.6
        # Context matters for technical entities
        assert config.graph_neighborhood == 0.4

    def test_weight_range_validation(self):
        """Test weight range validation."""
        with pytest.raises(ValueError):
            WeightConfiguration(jaro_winkler=1.5)

        with pytest.raises(ValueError):
            WeightConfiguration(embedding_cosine=-0.1)


class TestScoreComputation:
    """Tests for ScoreComputation request model."""

    def test_default_computation_flags(self):
        """Test default computation flags."""
        request = ScoreComputation(
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
        )

        assert request.compute_string is True
        assert request.compute_phonetic is True
        assert request.compute_semantic is True
        assert request.compute_graph is False  # Off by default (expensive)

    def test_with_custom_weights(self):
        """Test computation request with custom weights."""
        request = ScoreComputation(
            entity_a_id=uuid4(),
            entity_b_id=uuid4(),
            weight_config=WeightConfiguration.for_person_entities(),
        )

        assert request.weight_config is not None
        assert request.weight_config.jaro_winkler == 0.4


class TestSimilarityThresholds:
    """Tests for SimilarityThresholds model."""

    def test_default_thresholds(self):
        """Test default threshold values."""
        thresholds = SimilarityThresholds()

        assert thresholds.auto_merge == 0.90
        assert thresholds.review_required == 0.50
        assert thresholds.reject_below == 0.30

    def test_get_decision_auto_merge(self):
        """Test decision for high confidence."""
        thresholds = SimilarityThresholds()

        assert thresholds.get_decision(0.95) == "auto_merge"
        assert thresholds.get_decision(0.90) == "auto_merge"

    def test_get_decision_review(self):
        """Test decision for medium confidence."""
        thresholds = SimilarityThresholds()

        assert thresholds.get_decision(0.75) == "review"
        assert thresholds.get_decision(0.50) == "review"
        assert thresholds.get_decision(0.89) == "review"

    def test_get_decision_reject(self):
        """Test decision for low confidence."""
        thresholds = SimilarityThresholds()

        assert thresholds.get_decision(0.29) == "reject"
        assert thresholds.get_decision(0.10) == "reject"
        assert thresholds.get_decision(0.0) == "reject"

    def test_custom_thresholds(self):
        """Test custom threshold values."""
        thresholds = SimilarityThresholds(
            auto_merge=0.95,
            review_required=0.60,
            reject_below=0.40,
        )

        assert thresholds.get_decision(0.95) == "auto_merge"
        assert thresholds.get_decision(0.80) == "review"
        assert thresholds.get_decision(0.35) == "reject"

    def test_threshold_range_validation(self):
        """Test threshold value validation."""
        with pytest.raises(ValueError):
            SimilarityThresholds(auto_merge=1.5)

        with pytest.raises(ValueError):
            SimilarityThresholds(review_required=-0.1)
