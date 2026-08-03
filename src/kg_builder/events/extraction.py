"""
Domain events for the knowledge graph extraction pipeline.

These events track the lifecycle of an extraction process, from
initial request through to completion or failure. They are designed
to drive the event-sourced extraction architecture.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from eventsource import register_event

from kg_builder.events.base import TenantDomainEvent


# =============================================================================
# Extraction Lifecycle Events
# =============================================================================


@register_event
class ExtractionRequested(TenantDomainEvent):
    """Emitted when extraction is requested for a scraped page."""

    event_type: str = "ExtractionRequested"
    aggregate_type: str = "ExtractionProcess"

    page_id: UUID
    page_url: str
    content_hash: str
    extraction_config: dict = {}
    requested_at: datetime


@register_event
class ExtractionStarted(TenantDomainEvent):
    """Emitted when extraction processing begins."""

    event_type: str = "ExtractionStarted"
    aggregate_type: str = "ExtractionProcess"

    page_id: UUID
    worker_id: str
    started_at: datetime


@register_event
class ExtractionCompleted(TenantDomainEvent):
    """Emitted when extraction completes successfully."""

    event_type: str = "ExtractionCompleted"
    aggregate_type: str = "ExtractionProcess"

    page_id: UUID
    entity_count: int
    relationship_count: int
    duration_ms: int
    extraction_method: str
    completed_at: datetime


@register_event
class ExtractionProcessFailed(TenantDomainEvent):
    """Emitted when extraction fails.

    Note: Named ExtractionProcessFailed to differentiate from the existing
    ExtractionFailed event in scraping.py which is tied to ScrapedPage aggregate.
    This event is for the ExtractionProcess aggregate with additional retry logic.
    """

    event_type: str = "ExtractionProcessFailed"
    aggregate_type: str = "ExtractionProcess"

    page_id: UUID
    error_message: str
    error_type: str
    retry_count: int = 0
    retryable: bool = True
    failed_at: datetime


@register_event
class ExtractionRetryScheduled(TenantDomainEvent):
    """Emitted when a failed extraction is scheduled for retry."""

    event_type: str = "ExtractionRetryScheduled"
    aggregate_type: str = "ExtractionProcess"

    page_id: UUID
    retry_number: int
    scheduled_for: datetime
    backoff_seconds: float


# =============================================================================
# Relationship Events
# =============================================================================


@register_event
class RelationshipDiscovered(TenantDomainEvent):
    """Emitted when a relationship between entities is discovered."""

    event_type: str = "RelationshipDiscovered"
    aggregate_type: str = "ExtractionProcess"

    relationship_id: UUID
    page_id: UUID
    source_entity_name: str
    target_entity_name: str
    relationship_type: str
    confidence_score: float
    context: Optional[str] = None


# =============================================================================
# Batch Events (for performance optimization)
# =============================================================================


@register_event
class ExtractionBatchStarted(TenantDomainEvent):
    """Emitted when a batch extraction job starts."""

    event_type: str = "ExtractionBatchStarted"
    aggregate_type: str = "ExtractionBatch"

    batch_id: UUID
    page_ids: list[UUID]
    total_pages: int
    started_at: datetime


@register_event
class ExtractionBatchCompleted(TenantDomainEvent):
    """Emitted when a batch extraction job completes."""

    event_type: str = "ExtractionBatchCompleted"
    aggregate_type: str = "ExtractionBatch"

    batch_id: UUID
    successful_count: int
    failed_count: int
    total_entities: int
    total_relationships: int
    duration_ms: int
    completed_at: datetime
