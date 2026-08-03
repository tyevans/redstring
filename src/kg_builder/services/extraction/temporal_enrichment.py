"""
Temporal Enrichment Service for entity extraction.

This service enriches extracted entities with temporal data by:
1. Parsing LLM-extracted temporal expressions using TemporalParserService
2. Populating standardized temporal fields on entities
3. Tracking metrics for temporal extraction success/failure

See ADR-025 for design decisions.

Example usage:
    from kg_builder.services.extraction.temporal_enrichment import TemporalEnrichmentService

    enrichment = TemporalEnrichmentService()
    enriched_entities = await enrichment.enrich_entities(extraction_result.entities)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kg_builder.extraction.schemas import ExtractedEntitySchema, TemporalEventProperties
from kg_builder.schemas.timeline import UncertaintyMarker
from kg_builder.services.temporal_parser import TemporalParserService, create_temporal_parser

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)


# Mapping from TemporalEventProperties qualifier to UncertaintyMarker
QUALIFIER_TO_UNCERTAINTY: dict[str, UncertaintyMarker] = {
    "before": UncertaintyMarker.BEFORE,
    "after": UncertaintyMarker.AFTER,
    "around": UncertaintyMarker.APPROXIMATE,
    "approximately": UncertaintyMarker.APPROXIMATE,
    "about": UncertaintyMarker.APPROXIMATE,
    "roughly": UncertaintyMarker.APPROXIMATE,
    "circa": UncertaintyMarker.CIRCA,
    "during": UncertaintyMarker.APPROXIMATE,
    "until": UncertaintyMarker.BEFORE,
    "since": UncertaintyMarker.AFTER,
}


@dataclass
class TemporalEnrichmentResult:
    """Result of temporal enrichment for a single entity.

    Attributes:
        start_date: Parsed start date/time (UTC)
        end_date: Parsed end date/time (UTC)
        date_precision: Precision level of the date
        uncertainty_marker: Uncertainty indicator
        original_temporal_text: Original temporal expression
        sequence_position: Ordinal position in sequence
        confidence: Confidence in the temporal data (0.0 - 1.0)
        enriched: Whether any temporal data was added
        parse_method: Method used for parsing
    """

    start_date: datetime | None = None
    end_date: datetime | None = None
    date_precision: str | None = None
    uncertainty_marker: str | None = None
    original_temporal_text: str | None = None
    sequence_position: int | None = None
    confidence: float = 0.0
    enriched: bool = False
    parse_method: str | None = None


@dataclass
class TemporalEnrichmentStats:
    """Statistics for a batch of temporal enrichments.

    Attributes:
        total_entities: Total entities processed
        entities_with_temporal: Entities that had temporal data from LLM
        entities_enriched: Entities successfully enriched with parsed dates
        entities_parse_failed: Entities where parsing failed
        entities_sequence_only: Entities with only sequence position
    """

    total_entities: int = 0
    entities_with_temporal: int = 0
    entities_enriched: int = 0
    entities_parse_failed: int = 0
    entities_sequence_only: int = 0


class TemporalEnrichmentService:
    """Service for enriching extracted entities with temporal data.

    Takes entities with LLM-extracted temporal expressions and:
    1. Parses the temporal expressions using TemporalParserService
    2. Normalizes dates to UTC
    3. Infers precision and uncertainty
    4. Tracks metrics for success/failure rates

    Attributes:
        parser: The temporal parser service instance
    """

    def __init__(
        self,
        parser: TemporalParserService | None = None,
    ):
        """Initialize the temporal enrichment service.

        Args:
            parser: Optional parser service (creates default if not provided)
        """
        self.parser = parser or create_temporal_parser()

    def enrich_entity(
        self,
        entity: ExtractedEntitySchema,
    ) -> TemporalEnrichmentResult:
        """Enrich a single entity with temporal data.

        Takes the temporal property from the entity (if present) and
        parses/enriches it using the temporal parser.

        Args:
            entity: The extracted entity to enrich

        Returns:
            TemporalEnrichmentResult with parsed temporal data
        """
        result = TemporalEnrichmentResult()

        # Check if entity has temporal data
        if entity.temporal is None:
            logger.debug(
                "Entity '%s' has no temporal data to enrich",
                entity.name,
            )
            return result

        temporal = entity.temporal

        # Store original text
        if temporal.temporal_expression:
            result.original_temporal_text = temporal.temporal_expression

        # Store sequence position
        if temporal.sequence_position is not None:
            result.sequence_position = temporal.sequence_position
            result.enriched = True

        # If LLM provided a parseable event_date, use it
        if temporal.event_date:
            parse_result = self.parser.parse(temporal.event_date)

            if parse_result.start_date:
                result.start_date = parse_result.start_date
                result.date_precision = parse_result.precision.value
                result.confidence = parse_result.confidence
                result.parse_method = parse_result.parse_method
                result.enriched = True

                # Handle end date
                if temporal.end_date:
                    end_parse = self.parser.parse(temporal.end_date)
                    if end_parse.start_date:
                        result.end_date = end_parse.start_date
                elif parse_result.end_date:
                    result.end_date = parse_result.end_date

                # Determine uncertainty
                result.uncertainty_marker = self._determine_uncertainty(
                    temporal,
                    parse_result.uncertainty,
                )

                logger.debug(
                    "Enriched entity '%s' with parsed date: %s (precision: %s, uncertainty: %s)",
                    entity.name,
                    result.start_date,
                    result.date_precision,
                    result.uncertainty_marker,
                )

        # If event_date parsing failed, try parsing the temporal expression directly
        elif temporal.temporal_expression and not result.start_date:
            parse_result = self.parser.parse(temporal.temporal_expression)

            if parse_result.start_date:
                result.start_date = parse_result.start_date
                result.end_date = parse_result.end_date
                result.date_precision = parse_result.precision.value
                result.confidence = parse_result.confidence
                result.parse_method = parse_result.parse_method
                result.enriched = True

                # Determine uncertainty
                result.uncertainty_marker = self._determine_uncertainty(
                    temporal,
                    parse_result.uncertainty,
                )

                logger.debug(
                    "Enriched entity '%s' from temporal expression: %s",
                    entity.name,
                    result.start_date,
                )
            else:
                logger.debug(
                    "Failed to parse temporal expression for entity '%s': '%s'",
                    entity.name,
                    temporal.temporal_expression,
                )

        return result

    def _determine_uncertainty(
        self,
        temporal: TemporalEventProperties,
        parsed_uncertainty: UncertaintyMarker,
    ) -> str:
        """Determine the uncertainty marker from LLM data and parsed result.

        Combines information from:
        1. LLM's is_approximate flag
        2. LLM's temporal_qualifier
        3. Parser's detected uncertainty

        Args:
            temporal: LLM-extracted temporal properties
            parsed_uncertainty: Uncertainty detected by parser

        Returns:
            String representation of the uncertainty marker
        """
        # If LLM explicitly said it's approximate, use that
        if temporal.is_approximate:
            # Check if there's a more specific qualifier
            if temporal.temporal_qualifier:
                qualifier_lower = temporal.temporal_qualifier.lower()
                if qualifier_lower in QUALIFIER_TO_UNCERTAINTY:
                    return QUALIFIER_TO_UNCERTAINTY[qualifier_lower].value
            return UncertaintyMarker.APPROXIMATE.value

        # Check temporal qualifier from LLM
        if temporal.temporal_qualifier:
            qualifier_lower = temporal.temporal_qualifier.lower()
            if qualifier_lower in QUALIFIER_TO_UNCERTAINTY:
                return QUALIFIER_TO_UNCERTAINTY[qualifier_lower].value

        # Fall back to parser's detection
        return parsed_uncertainty.value

    def enrich_entities(
        self,
        entities: list[ExtractedEntitySchema],
    ) -> tuple[list[TemporalEnrichmentResult], TemporalEnrichmentStats]:
        """Enrich a batch of entities with temporal data.

        Args:
            entities: List of extracted entities to enrich

        Returns:
            Tuple of (enrichment results, statistics)
        """
        stats = TemporalEnrichmentStats(total_entities=len(entities))
        results: list[TemporalEnrichmentResult] = []

        for entity in entities:
            result = self.enrich_entity(entity)
            results.append(result)

            # Update statistics
            if entity.temporal is not None:
                stats.entities_with_temporal += 1

                if result.enriched:
                    if result.start_date:
                        stats.entities_enriched += 1
                    elif result.sequence_position is not None:
                        stats.entities_sequence_only += 1
                else:
                    stats.entities_parse_failed += 1

        logger.info(
            "Temporal enrichment complete: %d/%d entities enriched, %d sequence-only, %d parse failures",
            stats.entities_enriched,
            stats.entities_with_temporal,
            stats.entities_sequence_only,
            stats.entities_parse_failed,
        )

        return results, stats


# Module-level singleton instance
_service: TemporalEnrichmentService | None = None


def get_temporal_enrichment_service() -> TemporalEnrichmentService:
    """Get the singleton temporal enrichment service instance.

    Returns:
        The global TemporalEnrichmentService instance
    """
    global _service
    if _service is None:
        _service = TemporalEnrichmentService()
    return _service


def reset_temporal_enrichment_service() -> None:
    """Reset the singleton service instance.

    Primarily useful for testing to ensure a fresh instance.
    """
    global _service
    _service = None
