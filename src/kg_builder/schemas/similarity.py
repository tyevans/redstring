"""
Similarity scoring schemas for entity consolidation.

This module defines Pydantic models for representing similarity scores
between entity pairs during the consolidation process. It includes:

- Individual similarity type scores (string, phonetic, semantic, graph)
- Weighted combination scoring
- Confidence calculations with uncertainty quantification
- Score breakdown for human review transparency

These schemas are used throughout the consolidation pipeline:
- BlockingEngine outputs SimilarityScores
- MergeService uses scores for decision making
- Review queue displays score breakdowns to humans
- Events record scores for audit and learning
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class SimilarityType(str, Enum):
    """Types of similarity metrics that can be computed."""

    # String-based similarity metrics
    JARO_WINKLER = "jaro_winkler"
    LEVENSHTEIN = "levenshtein"
    DAMERAU_LEVENSHTEIN = "damerau_levenshtein"
    NORMALIZED_EXACT = "normalized_exact"
    TRIGRAM = "trigram"

    # Phonetic similarity metrics
    SOUNDEX = "soundex"
    METAPHONE = "metaphone"
    NYSIIS = "nysiis"

    # Semantic similarity metrics
    EMBEDDING_COSINE = "embedding_cosine"
    EMBEDDING_EUCLIDEAN = "embedding_euclidean"

    # Graph-based similarity metrics
    GRAPH_NEIGHBORHOOD = "graph_neighborhood"
    GRAPH_CO_OCCURRENCE = "graph_co_occurrence"

    # Contextual signals
    SAME_PAGE = "same_page"
    TYPE_MATCH = "type_match"
    PROPERTY_OVERLAP = "property_overlap"

    # Combined/composite scores
    FAST_COMPOSITE = "fast_composite"
    FULL_COMPOSITE = "full_composite"


class SimilarityScore(BaseModel):
    """
    A single similarity score with metadata.

    Represents one similarity measurement between two entities,
    including the metric type, raw score, weight, and contribution
    to the final combined score.

    Attributes:
        similarity_type: The type of similarity metric
        raw_score: The raw similarity score (0.0-1.0)
        weight: Weight for this score in combination (0.0-1.0)
        weighted_score: raw_score * weight
        is_computed: Whether this score was actually computed
        computation_time_ms: Time to compute in milliseconds (optional)
    """

    similarity_type: SimilarityType = Field(
        description="Type of similarity metric"
    )
    raw_score: float = Field(
        description="Raw similarity score (0.0-1.0)",
        ge=0.0,
        le=1.0,
    )
    weight: float = Field(
        description="Weight for combination (0.0-1.0)",
        ge=0.0,
        le=1.0,
        default=1.0,
    )
    weighted_score: float = Field(
        description="Weighted score (raw_score * weight)",
        ge=0.0,
        le=1.0,
        default=0.0,
    )
    is_computed: bool = Field(
        description="Whether this score was computed (vs skipped/unavailable)",
        default=True,
    )
    computation_time_ms: Optional[float] = Field(
        description="Computation time in milliseconds",
        default=None,
        ge=0.0,
    )

    @model_validator(mode="after")
    def compute_weighted_score(self) -> "SimilarityScore":
        """Compute weighted_score if not provided."""
        if self.weighted_score == 0.0 and self.raw_score > 0.0:
            # Use object.__setattr__ to bypass frozen model
            object.__setattr__(
                self, "weighted_score", self.raw_score * self.weight
            )
        return self


class StringSimilarityScores(BaseModel):
    """
    Collection of string-based similarity scores.

    Groups all string similarity metrics for clearer organization
    in the score breakdown.

    Attributes:
        jaro_winkler: Jaro-Winkler similarity (good for names)
        levenshtein: Levenshtein edit distance normalized
        damerau_levenshtein: Damerau-Levenshtein (handles transpositions)
        normalized_exact: Exact match on normalized strings
        trigram: Trigram similarity (q-gram based)
    """

    jaro_winkler: Optional[SimilarityScore] = Field(
        description="Jaro-Winkler similarity score",
        default=None,
    )
    levenshtein: Optional[SimilarityScore] = Field(
        description="Normalized Levenshtein similarity",
        default=None,
    )
    damerau_levenshtein: Optional[SimilarityScore] = Field(
        description="Damerau-Levenshtein similarity (handles transpositions)",
        default=None,
    )
    normalized_exact: Optional[SimilarityScore] = Field(
        description="Exact match on normalized strings",
        default=None,
    )
    trigram: Optional[SimilarityScore] = Field(
        description="Trigram (q-gram) similarity",
        default=None,
    )

    def get_best_score(self) -> Optional[SimilarityScore]:
        """Return the highest scoring string similarity metric."""
        scores = [
            s
            for s in [
                self.jaro_winkler,
                self.levenshtein,
                self.damerau_levenshtein,
                self.normalized_exact,
                self.trigram,
            ]
            if s is not None and s.is_computed
        ]
        return max(scores, key=lambda s: s.raw_score) if scores else None

    def get_average_score(self) -> float:
        """Return average of computed string similarity scores."""
        scores = [
            s.raw_score
            for s in [
                self.jaro_winkler,
                self.levenshtein,
                self.damerau_levenshtein,
                self.normalized_exact,
                self.trigram,
            ]
            if s is not None and s.is_computed
        ]
        return sum(scores) / len(scores) if scores else 0.0


class PhoneticSimilarityScores(BaseModel):
    """
    Collection of phonetic similarity scores.

    Phonetic similarity captures entities that sound alike
    but may be spelled differently (e.g., "Jon" vs "John").

    Attributes:
        soundex: Soundex phonetic encoding match
        metaphone: Metaphone phonetic encoding match
        nysiis: NYSIIS phonetic encoding match
    """

    soundex: Optional[SimilarityScore] = Field(
        description="Soundex phonetic similarity",
        default=None,
    )
    metaphone: Optional[SimilarityScore] = Field(
        description="Metaphone phonetic similarity",
        default=None,
    )
    nysiis: Optional[SimilarityScore] = Field(
        description="NYSIIS phonetic similarity",
        default=None,
    )

    def any_match(self) -> bool:
        """Check if any phonetic encoding matches."""
        for score in [self.soundex, self.metaphone, self.nysiis]:
            if score is not None and score.is_computed and score.raw_score >= 1.0:
                return True
        return False


class SemanticSimilarityScores(BaseModel):
    """
    Collection of semantic/embedding-based similarity scores.

    Semantic similarity uses vector embeddings to capture
    meaning similarity beyond lexical matching.

    Attributes:
        embedding_cosine: Cosine similarity between embedding vectors
        embedding_euclidean: Euclidean distance converted to similarity
    """

    embedding_cosine: Optional[SimilarityScore] = Field(
        description="Cosine similarity between embeddings",
        default=None,
    )
    embedding_euclidean: Optional[SimilarityScore] = Field(
        description="Euclidean similarity between embeddings",
        default=None,
    )

    def has_embedding_score(self) -> bool:
        """Check if any embedding similarity was computed."""
        return any(
            s is not None and s.is_computed
            for s in [self.embedding_cosine, self.embedding_euclidean]
        )

    def get_primary_score(self) -> Optional[SimilarityScore]:
        """Get primary embedding score (prefer cosine)."""
        if self.embedding_cosine and self.embedding_cosine.is_computed:
            return self.embedding_cosine
        if self.embedding_euclidean and self.embedding_euclidean.is_computed:
            return self.embedding_euclidean
        return None


class GraphSimilarityScores(BaseModel):
    """
    Collection of graph-based similarity scores.

    Graph similarity examines the neighborhood structure
    of entities in the knowledge graph.

    Attributes:
        neighborhood: Jaccard similarity of entity neighborhoods
        co_occurrence: Co-occurrence frequency on same pages/contexts
    """

    neighborhood: Optional[SimilarityScore] = Field(
        description="Graph neighborhood Jaccard similarity",
        default=None,
    )
    co_occurrence: Optional[SimilarityScore] = Field(
        description="Co-occurrence similarity score",
        default=None,
    )

    def has_graph_score(self) -> bool:
        """Check if any graph similarity was computed."""
        return any(
            s is not None and s.is_computed
            for s in [self.neighborhood, self.co_occurrence]
        )


class ContextualSignals(BaseModel):
    """
    Contextual signals that modify similarity interpretation.

    These are not similarity metrics per se, but contextual
    information that affects the confidence of similarity.

    Attributes:
        same_page: Whether entities came from the same source page
        type_match: Whether entity types match exactly
        property_overlap: Overlap in entity properties
    """

    same_page: Optional[SimilarityScore] = Field(
        description="Same source page signal (1.0 if same, 0.0 otherwise)",
        default=None,
    )
    type_match: Optional[SimilarityScore] = Field(
        description="Entity type match signal (1.0 if same, 0.0 otherwise)",
        default=None,
    )
    property_overlap: Optional[SimilarityScore] = Field(
        description="Property key overlap ratio",
        default=None,
    )

    @property
    def are_same_type(self) -> bool:
        """Check if entities are of the same type."""
        return (
            self.type_match is not None
            and self.type_match.is_computed
            and self.type_match.raw_score >= 1.0
        )

    @property
    def are_from_same_page(self) -> bool:
        """Check if entities came from the same page."""
        return (
            self.same_page is not None
            and self.same_page.is_computed
            and self.same_page.raw_score >= 1.0
        )


class SimilarityScores(BaseModel):
    """
    Complete similarity score collection for an entity pair.

    This is the main model used throughout the consolidation pipeline
    to represent all computed similarity information between two entities.

    Attributes:
        entity_a_id: First entity in the pair
        entity_b_id: Second entity in the pair
        string_scores: String-based similarity scores
        phonetic_scores: Phonetic similarity scores
        semantic_scores: Embedding-based similarity scores
        graph_scores: Graph neighborhood similarity scores
        contextual: Contextual signals
        combined_score: Final weighted combination score
        confidence: Confidence in the combined score
        uncertainty: Uncertainty range for confidence
        blocking_keys: Which blocking keys matched this pair
        computation_time_ms: Total computation time
    """

    entity_a_id: UUID = Field(description="First entity in the pair")
    entity_b_id: UUID = Field(description="Second entity in the pair")

    # Grouped scores by category
    string_scores: StringSimilarityScores = Field(
        description="String-based similarity scores",
        default_factory=StringSimilarityScores,
    )
    phonetic_scores: PhoneticSimilarityScores = Field(
        description="Phonetic similarity scores",
        default_factory=PhoneticSimilarityScores,
    )
    semantic_scores: SemanticSimilarityScores = Field(
        description="Semantic/embedding similarity scores",
        default_factory=SemanticSimilarityScores,
    )
    graph_scores: GraphSimilarityScores = Field(
        description="Graph-based similarity scores",
        default_factory=GraphSimilarityScores,
    )
    contextual: ContextualSignals = Field(
        description="Contextual signals",
        default_factory=ContextualSignals,
    )

    # Combined results
    combined_score: float = Field(
        description="Final weighted combination score (0.0-1.0)",
        ge=0.0,
        le=1.0,
        default=0.0,
    )
    confidence: float = Field(
        description="Confidence in the combined score (0.0-1.0)",
        ge=0.0,
        le=1.0,
        default=0.0,
    )
    uncertainty: float = Field(
        description="Uncertainty range (+/- from confidence)",
        ge=0.0,
        le=0.5,
        default=0.0,
    )

    # Metadata
    blocking_keys: list[str] = Field(
        description="Blocking keys that matched this pair",
        default_factory=list,
    )
    computation_time_ms: Optional[float] = Field(
        description="Total computation time in milliseconds",
        default=None,
        ge=0.0,
    )

    def to_dict(self) -> dict:
        """
        Convert scores to a flat dictionary for event/JSON storage.

        Returns a simplified dictionary suitable for storing in
        JSONB columns or event payloads.
        """
        result = {}

        # Add string scores
        if self.string_scores.jaro_winkler:
            result["jaro_winkler"] = self.string_scores.jaro_winkler.raw_score
        if self.string_scores.levenshtein:
            result["levenshtein"] = self.string_scores.levenshtein.raw_score
        if self.string_scores.damerau_levenshtein:
            result["damerau_levenshtein"] = (
                self.string_scores.damerau_levenshtein.raw_score
            )
        if self.string_scores.normalized_exact:
            result["normalized_exact"] = self.string_scores.normalized_exact.raw_score
        if self.string_scores.trigram:
            result["trigram"] = self.string_scores.trigram.raw_score

        # Add phonetic scores
        if self.phonetic_scores.soundex:
            result["soundex"] = self.phonetic_scores.soundex.raw_score
        if self.phonetic_scores.metaphone:
            result["metaphone"] = self.phonetic_scores.metaphone.raw_score
        if self.phonetic_scores.nysiis:
            result["nysiis"] = self.phonetic_scores.nysiis.raw_score

        # Add semantic scores
        if self.semantic_scores.embedding_cosine:
            result["embedding_cosine"] = (
                self.semantic_scores.embedding_cosine.raw_score
            )
        if self.semantic_scores.embedding_euclidean:
            result["embedding_euclidean"] = (
                self.semantic_scores.embedding_euclidean.raw_score
            )

        # Add graph scores
        if self.graph_scores.neighborhood:
            result["graph_neighborhood"] = self.graph_scores.neighborhood.raw_score
        if self.graph_scores.co_occurrence:
            result["graph_co_occurrence"] = self.graph_scores.co_occurrence.raw_score

        # Add contextual signals
        if self.contextual.same_page:
            result["same_page"] = self.contextual.same_page.raw_score
        if self.contextual.type_match:
            result["type_match"] = self.contextual.type_match.raw_score
        if self.contextual.property_overlap:
            result["property_overlap"] = self.contextual.property_overlap.raw_score

        # Add combined results
        result["combined_score"] = self.combined_score
        result["confidence"] = self.confidence
        result["uncertainty"] = self.uncertainty

        return result

    @classmethod
    def from_dict(
        cls, entity_a_id: UUID, entity_b_id: UUID, data: dict
    ) -> "SimilarityScores":
        """
        Reconstruct SimilarityScores from a flat dictionary.

        Args:
            entity_a_id: First entity ID
            entity_b_id: Second entity ID
            data: Dictionary of score values

        Returns:
            Reconstructed SimilarityScores object
        """
        scores = cls(entity_a_id=entity_a_id, entity_b_id=entity_b_id)

        # Reconstruct string scores
        if "jaro_winkler" in data:
            scores.string_scores.jaro_winkler = SimilarityScore(
                similarity_type=SimilarityType.JARO_WINKLER,
                raw_score=data["jaro_winkler"],
            )
        if "levenshtein" in data:
            scores.string_scores.levenshtein = SimilarityScore(
                similarity_type=SimilarityType.LEVENSHTEIN,
                raw_score=data["levenshtein"],
            )
        if "damerau_levenshtein" in data:
            scores.string_scores.damerau_levenshtein = SimilarityScore(
                similarity_type=SimilarityType.DAMERAU_LEVENSHTEIN,
                raw_score=data["damerau_levenshtein"],
            )
        if "normalized_exact" in data:
            scores.string_scores.normalized_exact = SimilarityScore(
                similarity_type=SimilarityType.NORMALIZED_EXACT,
                raw_score=data["normalized_exact"],
            )
        if "trigram" in data:
            scores.string_scores.trigram = SimilarityScore(
                similarity_type=SimilarityType.TRIGRAM,
                raw_score=data["trigram"],
            )

        # Reconstruct phonetic scores
        if "soundex" in data:
            scores.phonetic_scores.soundex = SimilarityScore(
                similarity_type=SimilarityType.SOUNDEX,
                raw_score=data["soundex"],
            )
        if "metaphone" in data:
            scores.phonetic_scores.metaphone = SimilarityScore(
                similarity_type=SimilarityType.METAPHONE,
                raw_score=data["metaphone"],
            )
        if "nysiis" in data:
            scores.phonetic_scores.nysiis = SimilarityScore(
                similarity_type=SimilarityType.NYSIIS,
                raw_score=data["nysiis"],
            )

        # Reconstruct semantic scores
        if "embedding_cosine" in data:
            scores.semantic_scores.embedding_cosine = SimilarityScore(
                similarity_type=SimilarityType.EMBEDDING_COSINE,
                raw_score=data["embedding_cosine"],
            )
        if "embedding_euclidean" in data:
            scores.semantic_scores.embedding_euclidean = SimilarityScore(
                similarity_type=SimilarityType.EMBEDDING_EUCLIDEAN,
                raw_score=data["embedding_euclidean"],
            )

        # Reconstruct graph scores
        if "graph_neighborhood" in data:
            scores.graph_scores.neighborhood = SimilarityScore(
                similarity_type=SimilarityType.GRAPH_NEIGHBORHOOD,
                raw_score=data["graph_neighborhood"],
            )
        if "graph_co_occurrence" in data:
            scores.graph_scores.co_occurrence = SimilarityScore(
                similarity_type=SimilarityType.GRAPH_CO_OCCURRENCE,
                raw_score=data["graph_co_occurrence"],
            )

        # Reconstruct contextual signals
        if "same_page" in data:
            scores.contextual.same_page = SimilarityScore(
                similarity_type=SimilarityType.SAME_PAGE,
                raw_score=data["same_page"],
            )
        if "type_match" in data:
            scores.contextual.type_match = SimilarityScore(
                similarity_type=SimilarityType.TYPE_MATCH,
                raw_score=data["type_match"],
            )
        if "property_overlap" in data:
            scores.contextual.property_overlap = SimilarityScore(
                similarity_type=SimilarityType.PROPERTY_OVERLAP,
                raw_score=data["property_overlap"],
            )

        # Set combined results
        scores.combined_score = data.get("combined_score", 0.0)
        scores.confidence = data.get("confidence", 0.0)
        scores.uncertainty = data.get("uncertainty", 0.0)

        return scores

    def get_all_computed_scores(self) -> list[SimilarityScore]:
        """Get all computed similarity scores as a flat list."""
        scores = []

        # String scores
        for s in [
            self.string_scores.jaro_winkler,
            self.string_scores.levenshtein,
            self.string_scores.damerau_levenshtein,
            self.string_scores.normalized_exact,
            self.string_scores.trigram,
        ]:
            if s is not None and s.is_computed:
                scores.append(s)

        # Phonetic scores
        for s in [
            self.phonetic_scores.soundex,
            self.phonetic_scores.metaphone,
            self.phonetic_scores.nysiis,
        ]:
            if s is not None and s.is_computed:
                scores.append(s)

        # Semantic scores
        for s in [
            self.semantic_scores.embedding_cosine,
            self.semantic_scores.embedding_euclidean,
        ]:
            if s is not None and s.is_computed:
                scores.append(s)

        # Graph scores
        for s in [
            self.graph_scores.neighborhood,
            self.graph_scores.co_occurrence,
        ]:
            if s is not None and s.is_computed:
                scores.append(s)

        return scores

    @property
    def score_count(self) -> int:
        """Return the number of computed scores."""
        return len(self.get_all_computed_scores())

    @property
    def is_high_confidence(self) -> bool:
        """Check if this is a high confidence match (>= 0.9)."""
        return self.confidence >= 0.9

    @property
    def is_medium_confidence(self) -> bool:
        """Check if this is a medium confidence match (0.5-0.89)."""
        return 0.5 <= self.confidence < 0.9

    @property
    def is_low_confidence(self) -> bool:
        """Check if this is a low confidence match (< 0.5)."""
        return self.confidence < 0.5


class WeightConfiguration(BaseModel):
    """
    Configuration for similarity score weights.

    Defines how individual similarity scores should be weighted
    when computing the combined score. Weights can be tuned
    based on entity type and tenant preferences.

    Attributes:
        jaro_winkler: Weight for Jaro-Winkler similarity
        levenshtein: Weight for Levenshtein similarity
        normalized_exact: Weight for exact normalized match
        trigram: Weight for trigram similarity
        soundex: Weight for soundex match
        embedding_cosine: Weight for embedding cosine similarity
        graph_neighborhood: Weight for graph neighborhood similarity
        same_page_bonus: Bonus weight for same-page entities
        type_match_bonus: Bonus weight for type-matched entities
    """

    # String weights
    jaro_winkler: float = Field(default=0.3, ge=0.0, le=1.0)
    levenshtein: float = Field(default=0.2, ge=0.0, le=1.0)
    normalized_exact: float = Field(default=0.4, ge=0.0, le=1.0)
    trigram: float = Field(default=0.25, ge=0.0, le=1.0)

    # Phonetic weights
    soundex: float = Field(default=0.15, ge=0.0, le=1.0)
    metaphone: float = Field(default=0.15, ge=0.0, le=1.0)

    # Semantic weights
    embedding_cosine: float = Field(default=0.5, ge=0.0, le=1.0)

    # Graph weights
    graph_neighborhood: float = Field(default=0.3, ge=0.0, le=1.0)

    # Contextual bonus weights
    same_page_bonus: float = Field(default=0.1, ge=0.0, le=1.0)
    type_match_bonus: float = Field(default=0.2, ge=0.0, le=1.0)

    @classmethod
    def default(cls) -> "WeightConfiguration":
        """Return default weight configuration."""
        return cls()

    @classmethod
    def for_person_entities(cls) -> "WeightConfiguration":
        """Return weight configuration optimized for person entities."""
        return cls(
            jaro_winkler=0.4,  # Names benefit from Jaro-Winkler
            soundex=0.2,  # Phonetic important for names
            embedding_cosine=0.4,  # Semantic less critical for names
        )

    @classmethod
    def for_organization_entities(cls) -> "WeightConfiguration":
        """Return weight configuration optimized for organization entities."""
        return cls(
            normalized_exact=0.5,  # Org names often have exact matches
            trigram=0.3,  # Handle abbreviations
            embedding_cosine=0.4,  # Semantic can help with variations
        )

    @classmethod
    def for_technical_entities(cls) -> "WeightConfiguration":
        """Return weight configuration optimized for technical entities (classes, functions)."""
        return cls(
            normalized_exact=0.6,  # Technical names are often exact
            embedding_cosine=0.5,  # Semantic similarity important
            graph_neighborhood=0.4,  # Context matters for technical entities
        )


class ScoreComputation(BaseModel):
    """
    Request model for computing similarity scores.

    Used when requesting similarity computation between two entities.

    Attributes:
        entity_a_id: First entity ID
        entity_b_id: Second entity ID
        compute_string: Whether to compute string similarity
        compute_phonetic: Whether to compute phonetic similarity
        compute_semantic: Whether to compute embedding similarity
        compute_graph: Whether to compute graph similarity
        weight_config: Weight configuration to use
    """

    entity_a_id: UUID = Field(description="First entity ID")
    entity_b_id: UUID = Field(description="Second entity ID")

    compute_string: bool = Field(
        description="Compute string similarity metrics",
        default=True,
    )
    compute_phonetic: bool = Field(
        description="Compute phonetic similarity metrics",
        default=True,
    )
    compute_semantic: bool = Field(
        description="Compute embedding similarity",
        default=True,
    )
    compute_graph: bool = Field(
        description="Compute graph neighborhood similarity",
        default=False,
    )
    weight_config: Optional[WeightConfiguration] = Field(
        description="Weight configuration (uses defaults if not provided)",
        default=None,
    )


class SimilarityThresholds(BaseModel):
    """
    Threshold configuration for similarity-based decisions.

    Defines the confidence thresholds for automatic merge,
    human review, and rejection decisions.

    Attributes:
        auto_merge: Threshold for automatic merging (default: 0.90)
        review_required: Threshold for human review (default: 0.50)
        reject_below: Threshold below which to reject (default: 0.30)
    """

    auto_merge: float = Field(
        description="Auto-merge if confidence >= this threshold",
        default=0.90,
        ge=0.0,
        le=1.0,
    )
    review_required: float = Field(
        description="Queue for review if confidence >= this threshold",
        default=0.50,
        ge=0.0,
        le=1.0,
    )
    reject_below: float = Field(
        description="Reject if confidence < this threshold",
        default=0.30,
        ge=0.0,
        le=1.0,
    )

    @field_validator("review_required")
    @classmethod
    def review_less_than_auto(cls, v: float, info) -> float:
        """Ensure review threshold is less than auto merge threshold."""
        if hasattr(info, "data") and info.data.get("auto_merge"):
            if v >= info.data["auto_merge"]:
                raise ValueError(
                    "review_required must be less than auto_merge threshold"
                )
        return v

    @field_validator("reject_below")
    @classmethod
    def reject_less_than_review(cls, v: float, info) -> float:
        """Ensure reject threshold is less than review threshold."""
        if hasattr(info, "data") and info.data.get("review_required"):
            if v >= info.data["review_required"]:
                raise ValueError(
                    "reject_below must be less than review_required threshold"
                )
        return v

    def get_decision(self, confidence: float) -> str:
        """
        Determine the decision based on confidence score.

        Args:
            confidence: The confidence score (0.0-1.0)

        Returns:
            Decision string: "auto_merge", "review", or "reject"
        """
        if confidence >= self.auto_merge:
            return "auto_merge"
        elif confidence >= self.review_required:
            return "review"
        else:
            return "reject"
