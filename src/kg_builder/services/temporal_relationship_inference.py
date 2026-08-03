"""
Temporal Relationship Inference Service.

This service infers temporal relationships between events based on their
date information when relationships are not explicitly extracted from text.

The inference follows these rules:
- If event A ends before event B starts: A PRECEDES B
- If events A and B have overlapping date ranges: A OVERLAPS B
- If event A dates are entirely within event B dates: A DURING B
- If events A and B have identical date ranges: A CONCURRENT with B

See ADR-025 and the Timeline Chronology Extraction FRD for design decisions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class TemporalEvent(BaseModel):
    """Minimal event representation for inference purposes.

    Contains just the temporal information needed for relationship inference.
    """

    id: uuid.UUID
    name: str
    start_date: datetime | None = None
    end_date: datetime | None = None


class InferredTemporalRelationship(BaseModel):
    """A temporal relationship inferred from event dates.

    This represents a relationship that was computed from date comparison
    rather than extracted from text.
    """

    id: uuid.UUID
    source_event_id: uuid.UUID
    target_event_id: uuid.UUID
    source_event_name: str
    target_event_name: str
    relationship_type: Literal[
        "precedes", "follows", "during", "overlaps", "concurrent"
    ]
    confidence: float
    evidence: str
    is_inferred: bool = True


class TemporalRelationshipInferenceService:
    """Service for inferring temporal relationships from event dates.

    This service analyzes pairs of events with temporal data and infers
    relationships based on date comparison. Inferred relationships have
    lower confidence scores than explicitly extracted ones.

    Inference Rules:
    1. PRECEDES: A.end_date < B.start_date (A happens entirely before B)
    2. FOLLOWS: A.start_date > B.end_date (A happens entirely after B)
    3. DURING: A.start_date >= B.start_date AND A.end_date <= B.end_date (A within B)
    4. OVERLAPS: Date ranges partially overlap but neither contains the other
    5. CONCURRENT: A.start_date == B.start_date AND A.end_date == B.end_date

    Confidence Levels:
    - 0.95: Both events have exact date ranges (start and end)
    - 0.85: One event has a range, one has a single date
    - 0.75: Both events have only start dates
    - 0.65: One or both events have approximate dates
    """

    # Confidence levels for different date precision scenarios
    CONFIDENCE_FULL_RANGE = 0.95
    CONFIDENCE_PARTIAL_RANGE = 0.85
    CONFIDENCE_SINGLE_DATE = 0.75
    CONFIDENCE_APPROXIMATE = 0.65

    def __init__(self) -> None:
        """Initialize the inference service."""
        pass

    def infer_relationships(
        self,
        events: list[TemporalEvent],
        *,
        existing_relationship_pairs: set[tuple[uuid.UUID, uuid.UUID]] | None = None,
    ) -> list[InferredTemporalRelationship]:
        """Infer temporal relationships between events.

        Analyzes all pairs of temporal events and infers relationships
        based on their dates. Skips pairs that already have explicit
        relationships.

        Args:
            events: List of temporal events to analyze
            existing_relationship_pairs: Set of (source_id, target_id) pairs
                that already have explicit relationships (to avoid duplicates)

        Returns:
            List of inferred temporal relationships
        """
        if existing_relationship_pairs is None:
            existing_relationship_pairs = set()

        # Filter to only events with at least a start_date
        temporal_events = [e for e in events if e.start_date is not None]

        if len(temporal_events) < 2:
            logger.debug(
                "Not enough temporal events for inference",
                extra={"event_count": len(temporal_events)},
            )
            return []

        inferred = []

        # Compare each pair of events
        for i, event_a in enumerate(temporal_events):
            for event_b in temporal_events[i + 1 :]:
                # Skip if relationship already exists
                pair_ab = (event_a.id, event_b.id)
                pair_ba = (event_b.id, event_a.id)
                if pair_ab in existing_relationship_pairs or pair_ba in existing_relationship_pairs:
                    continue

                # Try to infer a relationship
                relationship = self._infer_single_relationship(event_a, event_b)
                if relationship:
                    inferred.append(relationship)

        logger.info(
            "Inferred temporal relationships",
            extra={
                "event_count": len(temporal_events),
                "inferred_count": len(inferred),
            },
        )

        return inferred

    def _infer_single_relationship(
        self,
        event_a: TemporalEvent,
        event_b: TemporalEvent,
    ) -> InferredTemporalRelationship | None:
        """Infer the temporal relationship between two events.

        Args:
            event_a: First event
            event_b: Second event

        Returns:
            Inferred relationship or None if no clear relationship
        """
        if event_a.start_date is None or event_b.start_date is None:
            return None

        # Get effective end dates (use start_date if no end_date)
        a_start = event_a.start_date
        a_end = event_a.end_date or event_a.start_date
        b_start = event_b.start_date
        b_end = event_b.end_date or event_b.start_date

        # Calculate base confidence
        confidence = self._calculate_confidence(event_a, event_b)

        # Check for CONCURRENT (identical dates)
        if a_start == b_start and a_end == b_end:
            return self._create_relationship(
                event_a,
                event_b,
                "concurrent",
                confidence,
                f"Same dates: {a_start.date()} to {a_end.date()}",
            )

        # Check for PRECEDES (A entirely before B)
        if a_end < b_start:
            return self._create_relationship(
                event_a,
                event_b,
                "precedes",
                confidence,
                f"'{event_a.name}' ends ({a_end.date()}) before '{event_b.name}' starts ({b_start.date()})",
            )

        # Check for FOLLOWS (A entirely after B)
        if a_start > b_end:
            return self._create_relationship(
                event_a,
                event_b,
                "follows",
                confidence,
                f"'{event_a.name}' starts ({a_start.date()}) after '{event_b.name}' ends ({b_end.date()})",
            )

        # Check for DURING (A contained within B)
        if a_start >= b_start and a_end <= b_end and (a_start != b_start or a_end != b_end):
            return self._create_relationship(
                event_a,
                event_b,
                "during",
                confidence,
                f"'{event_a.name}' ({a_start.date()}-{a_end.date()}) occurs during '{event_b.name}' ({b_start.date()}-{b_end.date()})",
            )

        # Check for DURING (B contained within A) - reverse direction
        if b_start >= a_start and b_end <= a_end and (b_start != a_start or b_end != a_end):
            return self._create_relationship(
                event_b,
                event_a,
                "during",
                confidence,
                f"'{event_b.name}' ({b_start.date()}-{b_end.date()}) occurs during '{event_a.name}' ({a_start.date()}-{a_end.date()})",
            )

        # Check for OVERLAPS (partial overlap)
        # A starts before B ends AND A ends after B starts (but neither contains the other)
        if a_start < b_end and a_end > b_start:
            # Determine which event starts first for consistent relationship direction
            if a_start <= b_start:
                return self._create_relationship(
                    event_a,
                    event_b,
                    "overlaps",
                    confidence,
                    f"'{event_a.name}' ({a_start.date()}-{a_end.date()}) overlaps with '{event_b.name}' ({b_start.date()}-{b_end.date()})",
                )
            else:
                return self._create_relationship(
                    event_b,
                    event_a,
                    "overlaps",
                    confidence,
                    f"'{event_b.name}' ({b_start.date()}-{b_end.date()}) overlaps with '{event_a.name}' ({a_start.date()}-{a_end.date()})",
                )

        return None

    def _calculate_confidence(
        self,
        event_a: TemporalEvent,
        event_b: TemporalEvent,
    ) -> float:
        """Calculate confidence based on date precision.

        Args:
            event_a: First event
            event_b: Second event

        Returns:
            Confidence score between 0.65 and 0.95
        """
        a_has_range = event_a.end_date is not None
        b_has_range = event_b.end_date is not None

        if a_has_range and b_has_range:
            return self.CONFIDENCE_FULL_RANGE
        elif a_has_range or b_has_range:
            return self.CONFIDENCE_PARTIAL_RANGE
        else:
            return self.CONFIDENCE_SINGLE_DATE

    def _create_relationship(
        self,
        source: TemporalEvent,
        target: TemporalEvent,
        relationship_type: Literal["precedes", "follows", "during", "overlaps", "concurrent"],
        confidence: float,
        evidence: str,
    ) -> InferredTemporalRelationship:
        """Create an inferred relationship.

        Args:
            source: Source event
            target: Target event
            relationship_type: Type of temporal relationship
            confidence: Confidence score
            evidence: Description of how relationship was inferred

        Returns:
            InferredTemporalRelationship object
        """
        return InferredTemporalRelationship(
            id=uuid.uuid4(),
            source_event_id=source.id,
            target_event_id=target.id,
            source_event_name=source.name,
            target_event_name=target.name,
            relationship_type=relationship_type,
            confidence=confidence,
            evidence=f"Inferred from dates: {evidence}",
            is_inferred=True,
        )


def get_temporal_relationship_inference_service() -> TemporalRelationshipInferenceService:
    """Factory function to get the inference service.

    Returns:
        TemporalRelationshipInferenceService instance
    """
    return TemporalRelationshipInferenceService()
