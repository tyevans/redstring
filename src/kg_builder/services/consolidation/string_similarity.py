"""
String-based similarity computation for entity consolidation.

This module provides Stage 2 (fast) similarity computation using
string matching and phonetic algorithms. These computations are fast
and don't require external services or models.

The module uses the jellyfish library for string similarity algorithms:
- Jaro-Winkler: Optimized for short strings/names
- Levenshtein: Edit distance-based similarity
- Damerau-Levenshtein: Handles transpositions
- Soundex/Metaphone/NYSIIS: Phonetic encoding similarity

This is Stage 2 of the consolidation pipeline, filtering candidates
from Stage 1 (blocking) before expensive Stage 3 operations
(embeddings, graph analysis).
"""

from __future__ import annotations

import logging
import time
import unicodedata
from typing import TYPE_CHECKING

import jellyfish

from kg_builder.schemas.similarity import (
    ContextualSignals,
    PhoneticSimilarityScores,
    SimilarityScore,
    SimilarityScores,
    SimilarityType,
    StringSimilarityScores,
    WeightConfiguration,
)

if TYPE_CHECKING:
    from kg_builder.models.extracted_entity import ExtractedEntity

logger = logging.getLogger(__name__)


def normalize_for_comparison(text: str) -> str:
    """
    Normalize text for similarity comparison.

    Applies:
    - Lowercase conversion
    - Unicode normalization (NFKD)
    - Accent/diacritic removal
    - Whitespace collapse
    - Leading/trailing whitespace removal

    Args:
        text: Text to normalize

    Returns:
        Normalized text string
    """
    if not text:
        return ""

    # Lowercase
    normalized = text.lower().strip()

    # Unicode normalization and accent removal
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))

    # Collapse multiple spaces
    normalized = " ".join(normalized.split())

    return normalized


def tokenize_name(name: str) -> list[str]:
    """
    Tokenize a name into individual tokens.

    Handles various separators:
    - Spaces: "John Smith" -> ["john", "smith"]
    - Underscores: "domain_event" -> ["domain", "event"]
    - CamelCase: "DomainEvent" -> ["domain", "event"]
    - Hyphens: "semi-colon" -> ["semi", "colon"]

    Args:
        name: Name to tokenize

    Returns:
        List of lowercase tokens
    """
    if not name:
        return []

    # Handle CamelCase: insert space before uppercase letters
    result = ""
    for i, char in enumerate(name):
        if char.isupper() and i > 0 and name[i - 1].islower():
            result += " "
        result += char

    # Replace common separators with spaces
    for sep in ["_", "-", ".", "/"]:
        result = result.replace(sep, " ")

    # Split and lowercase
    tokens = [t.lower().strip() for t in result.split() if t.strip()]

    return tokens


class StringSimilarityService:
    """
    Computes string-based similarity between entities.

    This class implements Stage 2 of the matching pipeline, computing
    fast string-based and phonetic similarity scores that don't require
    external models or services.

    Computed string scores:
    - jaro_winkler: String similarity optimized for short strings (0.0-1.0)
    - levenshtein: Edit distance normalized to similarity (0.0-1.0)
    - damerau_levenshtein: Like Levenshtein but handles transpositions
    - normalized_exact: Binary exact match on normalized names (0.0 or 1.0)
    - trigram: N-gram based similarity (0.0-1.0)

    Computed phonetic scores:
    - soundex: Soundex phonetic code match (0.0 or 1.0)
    - metaphone: Metaphone phonetic code match (0.0 or 1.0)
    - nysiis: NYSIIS phonetic code match (0.0 or 1.0)

    Contextual signals:
    - type_match: Entity types match (0.0 or 1.0)
    - same_page: Entities from same source page (0.0 or 1.0)
    - property_overlap: Overlap in entity properties (0.0-1.0)

    Example:
        service = StringSimilarityService()
        scores = service.compute_all(entity_a, entity_b)

        if scores.is_high_confidence:
            # Auto-merge candidate
            pass
        elif scores.is_medium_confidence:
            # Queue for review
            pass
    """

    def __init__(
        self,
        compute_all_string: bool = True,
        compute_phonetic: bool = True,
        compute_contextual: bool = True,
        weight_config: WeightConfiguration | None = None,
    ):
        """
        Initialize the string similarity service.

        Args:
            compute_all_string: Whether to compute all string metrics
                               (vs just Jaro-Winkler)
            compute_phonetic: Whether to compute phonetic metrics
            compute_contextual: Whether to compute contextual signals
            weight_config: Weight configuration for score combination
        """
        self.compute_all_string = compute_all_string
        self.compute_phonetic = compute_phonetic
        self.compute_contextual = compute_contextual
        self.weight_config = weight_config or WeightConfiguration.default()

    def compute_all(
        self,
        entity_a: ExtractedEntity,
        entity_b: ExtractedEntity,
        blocking_keys: list[str] | None = None,
    ) -> SimilarityScores:
        """
        Compute all similarity scores between two entities.

        This is the main entry point for Stage 2 similarity computation.
        Computes string, phonetic, and contextual scores.

        Args:
            entity_a: First entity
            entity_b: Second entity
            blocking_keys: Optional list of blocking keys that matched

        Returns:
            SimilarityScores with all Stage 2 scores populated
        """
        start_time = time.perf_counter()

        # Initialize the scores object
        scores = SimilarityScores(
            entity_a_id=entity_a.id,
            entity_b_id=entity_b.id,
            blocking_keys=blocking_keys or [],
        )

        # Compute string similarity scores
        scores.string_scores = self._compute_string_scores(entity_a, entity_b)

        # Compute phonetic similarity scores
        if self.compute_phonetic:
            scores.phonetic_scores = self._compute_phonetic_scores(entity_a, entity_b)

        # Compute contextual signals
        if self.compute_contextual:
            scores.contextual = self._compute_contextual_signals(entity_a, entity_b)

        # Compute combined score using fast metrics only
        scores.combined_score = self._compute_fast_composite(scores)
        scores.confidence = self._estimate_confidence(scores)

        # Track computation time
        execution_time = (time.perf_counter() - start_time) * 1000
        scores.computation_time_ms = execution_time

        logger.debug(
            f"String similarity computed for ({entity_a.id}, {entity_b.id}): "
            f"combined={scores.combined_score:.3f}, confidence={scores.confidence:.3f}, "
            f"time={execution_time:.2f}ms"
        )

        return scores

    def _compute_string_scores(
        self,
        entity_a: ExtractedEntity,
        entity_b: ExtractedEntity,
    ) -> StringSimilarityScores:
        """Compute string-based similarity scores."""
        name_a = entity_a.name
        name_b = entity_b.name
        normalized_a = entity_a.normalized_name
        normalized_b = entity_b.normalized_name

        scores = StringSimilarityScores()

        # Jaro-Winkler (always computed, optimized for names)
        jw_start = time.perf_counter()
        jw_score = self.compute_jaro_winkler(name_a, name_b)
        jw_time = (time.perf_counter() - jw_start) * 1000
        scores.jaro_winkler = SimilarityScore(
            similarity_type=SimilarityType.JARO_WINKLER,
            raw_score=jw_score,
            weight=self.weight_config.jaro_winkler,
            computation_time_ms=jw_time,
        )

        # Normalized exact match
        exact_score = 1.0 if normalized_a == normalized_b else 0.0
        scores.normalized_exact = SimilarityScore(
            similarity_type=SimilarityType.NORMALIZED_EXACT,
            raw_score=exact_score,
            weight=self.weight_config.normalized_exact,
        )

        if self.compute_all_string:
            # Levenshtein
            lev_start = time.perf_counter()
            lev_score = self.compute_levenshtein_ratio(name_a, name_b)
            lev_time = (time.perf_counter() - lev_start) * 1000
            scores.levenshtein = SimilarityScore(
                similarity_type=SimilarityType.LEVENSHTEIN,
                raw_score=lev_score,
                weight=self.weight_config.levenshtein,
                computation_time_ms=lev_time,
            )

            # Damerau-Levenshtein
            dl_start = time.perf_counter()
            dl_score = self.compute_damerau_levenshtein_ratio(name_a, name_b)
            dl_time = (time.perf_counter() - dl_start) * 1000
            scores.damerau_levenshtein = SimilarityScore(
                similarity_type=SimilarityType.DAMERAU_LEVENSHTEIN,
                raw_score=dl_score,
                computation_time_ms=dl_time,
            )

            # Trigram similarity
            tg_start = time.perf_counter()
            tg_score = self.compute_trigram_similarity(normalized_a, normalized_b)
            tg_time = (time.perf_counter() - tg_start) * 1000
            scores.trigram = SimilarityScore(
                similarity_type=SimilarityType.TRIGRAM,
                raw_score=tg_score,
                weight=self.weight_config.trigram,
                computation_time_ms=tg_time,
            )

        return scores

    def _compute_phonetic_scores(
        self,
        entity_a: ExtractedEntity,
        entity_b: ExtractedEntity,
    ) -> PhoneticSimilarityScores:
        """Compute phonetic similarity scores."""
        name_a = entity_a.name
        name_b = entity_b.name

        scores = PhoneticSimilarityScores()

        # Soundex
        soundex_a = self.compute_soundex(name_a)
        soundex_b = self.compute_soundex(name_b)
        soundex_match = 1.0 if soundex_a and soundex_a == soundex_b else 0.0
        scores.soundex = SimilarityScore(
            similarity_type=SimilarityType.SOUNDEX,
            raw_score=soundex_match,
            weight=self.weight_config.soundex,
        )

        # Metaphone
        metaphone_a = self.compute_metaphone(name_a)
        metaphone_b = self.compute_metaphone(name_b)
        metaphone_match = 1.0 if metaphone_a and metaphone_a == metaphone_b else 0.0
        scores.metaphone = SimilarityScore(
            similarity_type=SimilarityType.METAPHONE,
            raw_score=metaphone_match,
            weight=self.weight_config.metaphone,
        )

        # NYSIIS
        nysiis_a = self.compute_nysiis(name_a)
        nysiis_b = self.compute_nysiis(name_b)
        nysiis_match = 1.0 if nysiis_a and nysiis_a == nysiis_b else 0.0
        scores.nysiis = SimilarityScore(
            similarity_type=SimilarityType.NYSIIS,
            raw_score=nysiis_match,
        )

        return scores

    def _compute_contextual_signals(
        self,
        entity_a: ExtractedEntity,
        entity_b: ExtractedEntity,
    ) -> ContextualSignals:
        """Compute contextual signals."""
        signals = ContextualSignals()

        # Same page signal
        same_page = 1.0 if entity_a.source_page_id == entity_b.source_page_id else 0.0
        signals.same_page = SimilarityScore(
            similarity_type=SimilarityType.SAME_PAGE,
            raw_score=same_page,
            weight=self.weight_config.same_page_bonus,
        )

        # Type match signal
        type_match = 1.0 if entity_a.entity_type == entity_b.entity_type else 0.0
        signals.type_match = SimilarityScore(
            similarity_type=SimilarityType.TYPE_MATCH,
            raw_score=type_match,
            weight=self.weight_config.type_match_bonus,
        )

        # Property overlap
        props_a = set(entity_a.properties.keys()) if entity_a.properties else set()
        props_b = set(entity_b.properties.keys()) if entity_b.properties else set()
        if props_a or props_b:
            intersection = len(props_a & props_b)
            union = len(props_a | props_b)
            overlap = intersection / union if union > 0 else 0.0
        else:
            overlap = 0.0
        signals.property_overlap = SimilarityScore(
            similarity_type=SimilarityType.PROPERTY_OVERLAP,
            raw_score=overlap,
        )

        return signals

    def _compute_fast_composite(self, scores: SimilarityScores) -> float:
        """
        Compute fast composite score from Stage 2 metrics.

        Uses weighted combination of string and phonetic scores.

        Args:
            scores: SimilarityScores object with Stage 2 scores

        Returns:
            Combined score (0.0-1.0)
        """
        total_weight = 0.0
        weighted_sum = 0.0

        # String scores
        if scores.string_scores.jaro_winkler:
            s = scores.string_scores.jaro_winkler
            weighted_sum += s.raw_score * s.weight
            total_weight += s.weight

        if scores.string_scores.normalized_exact:
            s = scores.string_scores.normalized_exact
            weighted_sum += s.raw_score * s.weight
            total_weight += s.weight

        if scores.string_scores.trigram:
            s = scores.string_scores.trigram
            weighted_sum += s.raw_score * s.weight
            total_weight += s.weight

        # Phonetic scores (binary, so use as bonus)
        if scores.phonetic_scores.soundex and scores.phonetic_scores.soundex.raw_score:
            s = scores.phonetic_scores.soundex
            weighted_sum += s.raw_score * s.weight
            total_weight += s.weight

        # Contextual bonuses (only if positive)
        if scores.contextual.type_match and scores.contextual.type_match.raw_score:
            # Type match gives a bonus
            s = scores.contextual.type_match
            weighted_sum += s.raw_score * s.weight
            total_weight += s.weight

        if total_weight == 0:
            return 0.0

        combined = weighted_sum / total_weight

        # Ensure in valid range
        return max(0.0, min(1.0, combined))

    def _estimate_confidence(self, scores: SimilarityScores) -> float:
        """
        Estimate confidence in the combined score.

        Confidence is higher when:
        - Multiple metrics agree
        - Exact normalized match exists
        - Type match exists
        - Scores are not borderline (near 0.5)

        Args:
            scores: SimilarityScores object

        Returns:
            Confidence score (0.0-1.0)
        """
        confidence_factors = []

        # Factor 1: Exact match is very high confidence
        if (
            scores.string_scores.normalized_exact
            and scores.string_scores.normalized_exact.raw_score == 1.0
        ):
            confidence_factors.append(0.95)

        # Factor 2: Jaro-Winkler score (adjusted to confidence)
        if scores.string_scores.jaro_winkler:
            jw = scores.string_scores.jaro_winkler.raw_score
            # Higher JW = higher confidence
            jw_confidence = jw if jw > 0.7 else jw * 0.5
            confidence_factors.append(jw_confidence)

        # Factor 3: Phonetic agreement
        if scores.phonetic_scores.any_match():
            confidence_factors.append(0.8)

        # Factor 4: Type match adds confidence
        if scores.contextual.are_same_type:
            confidence_factors.append(0.7)

        # Factor 5: Same page reduces confidence (might be different entities)
        if scores.contextual.are_from_same_page:
            # Same page entities with similar names might NOT be the same
            confidence_factors.append(0.5)

        if not confidence_factors:
            return scores.combined_score  # Default to combined score

        # Use geometric mean for confidence (penalizes low factors more)
        product = 1.0
        for f in confidence_factors:
            product *= f
        confidence = product ** (1.0 / len(confidence_factors))

        return max(0.0, min(1.0, confidence))

    def filter_candidates(
        self,
        entity: ExtractedEntity,
        candidates: list[ExtractedEntity],
        threshold: float = 0.70,
    ) -> list[tuple[ExtractedEntity, SimilarityScores]]:
        """
        Compute scores and filter to candidates above threshold.

        This is the typical Stage 2 entry point - compute fast scores
        and filter to candidates worth sending to Stage 3.

        Args:
            entity: Source entity
            candidates: List of candidate entities
            threshold: Minimum combined_score to pass

        Returns:
            Filtered list of (candidate, scores) tuples above threshold,
            sorted by combined_score descending
        """
        results: list[tuple[ExtractedEntity, SimilarityScores]] = []

        for candidate in candidates:
            # Get blocking keys if tracked on candidate
            blocking_keys = getattr(candidate, "_blocking_keys", [])
            scores = self.compute_all(entity, candidate, blocking_keys)

            if scores.combined_score >= threshold:
                results.append((candidate, scores))

        # Sort by combined score descending
        results.sort(key=lambda x: x[1].combined_score, reverse=True)

        logger.debug(
            f"Filtered {len(candidates)} candidates to {len(results)} above threshold {threshold}"
        )

        return results

    def compute_batch(
        self,
        entity: ExtractedEntity,
        candidates: list[ExtractedEntity],
    ) -> list[tuple[ExtractedEntity, SimilarityScores]]:
        """
        Compute similarity scores for entity against multiple candidates.

        Args:
            entity: Source entity
            candidates: List of candidate entities

        Returns:
            List of (candidate, scores) tuples sorted by combined_score descending
        """
        results: list[tuple[ExtractedEntity, SimilarityScores]] = []

        for candidate in candidates:
            blocking_keys = getattr(candidate, "_blocking_keys", [])
            scores = self.compute_all(entity, candidate, blocking_keys)
            results.append((candidate, scores))

        # Sort by combined score descending
        results.sort(key=lambda x: x[1].combined_score, reverse=True)

        return results

    # -------------------------------------------------------------------------
    # Individual Similarity Functions
    # -------------------------------------------------------------------------

    @staticmethod
    def compute_jaro_winkler(str_a: str, str_b: str) -> float:
        """
        Compute Jaro-Winkler similarity between two strings.

        Jaro-Winkler is optimized for short strings and gives
        higher weight to common prefixes. Range: 0.0 to 1.0.

        Args:
            str_a: First string
            str_b: Second string

        Returns:
            Similarity score (0.0-1.0)
        """
        if not str_a or not str_b:
            return 0.0
        return jellyfish.jaro_winkler_similarity(str_a.lower(), str_b.lower())

    @staticmethod
    def compute_levenshtein_ratio(str_a: str, str_b: str) -> float:
        """
        Compute normalized Levenshtein similarity.

        Levenshtein distance counts insertions, deletions, and substitutions.
        Normalized to 0.0-1.0 range based on string lengths.

        Args:
            str_a: First string
            str_b: Second string

        Returns:
            Similarity score (0.0-1.0)
        """
        if not str_a and not str_b:
            return 1.0
        if not str_a or not str_b:
            return 0.0

        distance = jellyfish.levenshtein_distance(str_a.lower(), str_b.lower())
        max_len = max(len(str_a), len(str_b))
        return 1.0 - (distance / max_len)

    @staticmethod
    def compute_damerau_levenshtein_ratio(str_a: str, str_b: str) -> float:
        """
        Compute normalized Damerau-Levenshtein similarity.

        Like Levenshtein but also counts transpositions as single edits.
        Better for typo detection (e.g., "teh" vs "the").

        Args:
            str_a: First string
            str_b: Second string

        Returns:
            Similarity score (0.0-1.0)
        """
        if not str_a and not str_b:
            return 1.0
        if not str_a or not str_b:
            return 0.0

        distance = jellyfish.damerau_levenshtein_distance(str_a.lower(), str_b.lower())
        max_len = max(len(str_a), len(str_b))
        return 1.0 - (distance / max_len)

    @staticmethod
    def compute_trigram_similarity(str_a: str, str_b: str) -> float:
        """
        Compute trigram (3-gram) similarity between strings.

        Trigrams are 3-character substrings. Similarity is computed
        as Jaccard coefficient of trigram sets.

        Args:
            str_a: First string
            str_b: Second string

        Returns:
            Similarity score (0.0-1.0)
        """
        if not str_a or not str_b:
            return 0.0

        def get_trigrams(s: str) -> set:
            s = s.lower()
            # Pad string for edge trigrams
            s = f"  {s}  "
            return {s[i : i + 3] for i in range(len(s) - 2)}

        trigrams_a = get_trigrams(str_a)
        trigrams_b = get_trigrams(str_b)

        if not trigrams_a and not trigrams_b:
            return 1.0

        intersection = len(trigrams_a & trigrams_b)
        union = len(trigrams_a | trigrams_b)

        return intersection / union if union > 0 else 0.0

    @staticmethod
    def compute_soundex(name: str) -> str:
        """
        Compute Soundex phonetic code for a name.

        Soundex indexes names by sound as pronounced in English.
        Returns a 4-character code (letter + 3 digits).

        Args:
            name: Name to encode

        Returns:
            Soundex code (e.g., "R163" for "Robert")
        """
        if not name:
            return ""
        return jellyfish.soundex(name)

    @staticmethod
    def compute_metaphone(name: str) -> str:
        """
        Compute Metaphone phonetic code for a name.

        Metaphone is an improved phonetic algorithm that handles
        more English phonetic rules than Soundex.

        Args:
            name: Name to encode

        Returns:
            Metaphone code
        """
        if not name:
            return ""
        return jellyfish.metaphone(name)

    @staticmethod
    def compute_nysiis(name: str) -> str:
        """
        Compute NYSIIS phonetic code for a name.

        NYSIIS (New York State Identification and Intelligence System)
        handles some American name pronunciations better than Soundex.

        Args:
            name: Name to encode

        Returns:
            NYSIIS code
        """
        if not name:
            return ""
        return jellyfish.nysiis(name)


# -------------------------------------------------------------------------
# Convenience Functions
# -------------------------------------------------------------------------


def compute_string_similarity(
    entity_a: ExtractedEntity,
    entity_b: ExtractedEntity,
) -> SimilarityScores:
    """
    Convenience function for one-off similarity computation.

    Args:
        entity_a: First entity
        entity_b: Second entity

    Returns:
        SimilarityScores with Stage 2 scores populated
    """
    service = StringSimilarityService()
    return service.compute_all(entity_a, entity_b)


def compute_phonetic_similarity(name_a: str, name_b: str) -> dict[str, bool]:
    """
    Compute phonetic similarity between two names.

    Args:
        name_a: First name
        name_b: Second name

    Returns:
        Dictionary with phonetic match results
    """
    return {
        "soundex_match": jellyfish.soundex(name_a) == jellyfish.soundex(name_b),
        "metaphone_match": jellyfish.metaphone(name_a) == jellyfish.metaphone(name_b),
        "nysiis_match": jellyfish.nysiis(name_a) == jellyfish.nysiis(name_b),
    }
