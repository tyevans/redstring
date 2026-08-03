"""
Domain events for uploaded document lifecycle.

These events track the complete lifecycle of uploaded documents from
initial upload through parsing, extraction, and deletion.

All events extend TenantDomainEvent for multi-tenant isolation.
"""

from datetime import datetime
from uuid import UUID

from eventsource import register_event
from pydantic import Field

from kg_builder.events.base import TenantDomainEvent

# =============================================================================
# Document Upload Events
# =============================================================================


@register_event
class DocumentUploaded(TenantDomainEvent):
    """
    Emitted when a document is uploaded.

    This event records the initial upload of a document before parsing.

    Attributes:
        document_id: Unique identifier for this document
        project_id: Project the document belongs to
        created_by_user_id: User who uploaded the document
        filename: Original filename
        content_type: MIME content type
        file_size_bytes: Size of the file in bytes
        storage_bucket: Storage bucket where file is stored
        storage_key: Storage key/path for the file
        enable_timeline_extraction: Whether to extract temporal events
        uploaded_at: When the document was uploaded
    """

    event_type: str = "DocumentUploaded"
    aggregate_type: str = "UploadedDocument"

    document_id: UUID = Field(description="Unique document ID")
    project_id: UUID = Field(description="Project ID")
    created_by_user_id: UUID = Field(description="User who uploaded")
    filename: str = Field(description="Original filename")
    content_type: str = Field(description="MIME content type")
    file_size_bytes: int = Field(description="File size in bytes", ge=0)
    storage_bucket: str = Field(description="Storage bucket")
    storage_key: str = Field(description="Storage key/path")
    enable_timeline_extraction: bool = Field(
        description="Extract temporal events",
        default=False,
    )
    uploaded_at: datetime = Field(description="Upload timestamp")


# =============================================================================
# Document Parsing Events
# =============================================================================


@register_event
class DocumentParsingStarted(TenantDomainEvent):
    """
    Emitted when document parsing begins.

    This event is emitted when a worker starts parsing an uploaded document
    to extract text content.

    Attributes:
        document_id: Document being parsed
        worker_id: ID of the worker processing the document
        started_at: When parsing started
    """

    event_type: str = "DocumentParsingStarted"
    aggregate_type: str = "UploadedDocument"

    document_id: UUID = Field(description="Document ID")
    worker_id: str = Field(description="Worker processing document")
    started_at: datetime = Field(description="When parsing started")


@register_event
class DocumentParsingCompleted(TenantDomainEvent):
    """
    Emitted when document parsing completes successfully.

    This event records successful parsing of document content.

    Attributes:
        document_id: Document that was parsed
        text_content_length: Length of extracted text content
        page_count: Number of pages (for PDFs)
        duration_ms: Parsing duration in milliseconds
        completed_at: When parsing completed
    """

    event_type: str = "DocumentParsingCompleted"
    aggregate_type: str = "UploadedDocument"

    document_id: UUID = Field(description="Document ID")
    text_content_length: int = Field(description="Extracted text length", ge=0)
    page_count: int | None = Field(description="Number of pages (PDFs)", default=None)
    duration_ms: int = Field(description="Parsing duration (ms)", ge=0)
    completed_at: datetime = Field(description="When parsing completed")


@register_event
class DocumentParsingFailed(TenantDomainEvent):
    """
    Emitted when document parsing fails.

    This event records a failure during document parsing.

    Attributes:
        document_id: Document that failed to parse
        error_message: Human-readable error message
        error_type: Classification of error
        retryable: Whether this failure can be retried
        failed_at: When failure occurred
    """

    event_type: str = "DocumentParsingFailed"
    aggregate_type: str = "UploadedDocument"

    document_id: UUID = Field(description="Document ID")
    error_message: str = Field(description="Error message")
    error_type: str = Field(description="Error classification")
    retryable: bool = Field(description="Can be retried", default=True)
    failed_at: datetime = Field(description="When failure occurred")


# =============================================================================
# Document Extraction Events
# =============================================================================


@register_event
class DocumentExtractionStarted(TenantDomainEvent):
    """
    Emitted when entity extraction begins on a document.

    This event is emitted when entity extraction starts on parsed document content.

    Attributes:
        document_id: Document being processed
        worker_id: Worker handling extraction
        extraction_config: Configuration for extraction
        started_at: When extraction started
    """

    event_type: str = "DocumentExtractionStarted"
    aggregate_type: str = "UploadedDocument"

    document_id: UUID = Field(description="Document ID")
    worker_id: str = Field(description="Worker ID")
    extraction_config: dict = Field(
        description="Extraction configuration",
        default_factory=dict,
    )
    started_at: datetime = Field(description="When extraction started")


@register_event
class DocumentExtractionCompleted(TenantDomainEvent):
    """
    Emitted when entity extraction completes successfully.

    This event records successful extraction of entities from a document.

    Attributes:
        document_id: Document that was processed
        entity_count: Number of entities extracted
        relationship_count: Number of relationships discovered
        duration_ms: Extraction duration in milliseconds
        extraction_method: Method used for extraction
        completed_at: When extraction completed
    """

    event_type: str = "DocumentExtractionCompleted"
    aggregate_type: str = "UploadedDocument"

    document_id: UUID = Field(description="Document ID")
    entity_count: int = Field(description="Entities extracted", ge=0)
    relationship_count: int = Field(description="Relationships discovered", ge=0, default=0)
    duration_ms: int = Field(description="Extraction duration (ms)", ge=0)
    extraction_method: str = Field(description="Extraction method used")
    completed_at: datetime = Field(description="When extraction completed")


@register_event
class DocumentExtractionFailed(TenantDomainEvent):
    """
    Emitted when entity extraction fails.

    This event records a failure during entity extraction.

    Attributes:
        document_id: Document that failed extraction
        error_message: Human-readable error message
        error_type: Classification of error
        entities_extracted_before_failure: Entities extracted before failure
        retryable: Whether this failure can be retried
        failed_at: When failure occurred
    """

    event_type: str = "DocumentExtractionFailed"
    aggregate_type: str = "UploadedDocument"

    document_id: UUID = Field(description="Document ID")
    error_message: str = Field(description="Error message")
    error_type: str = Field(description="Error classification")
    entities_extracted_before_failure: int = Field(
        description="Entities extracted before failure",
        ge=0,
        default=0,
    )
    retryable: bool = Field(description="Can be retried", default=True)
    failed_at: datetime = Field(description="When failure occurred")


# =============================================================================
# Document Deletion Events
# =============================================================================


@register_event
class DocumentDeleted(TenantDomainEvent):
    """
    Emitted when a document is deleted.

    This event records the deletion of a document and its associated data.

    Attributes:
        document_id: Document being deleted
        deleted_by_user_id: User who initiated deletion
        cascade_entities: Whether associated entities were deleted
        entity_count_deleted: Number of entities deleted if cascaded
        deleted_at: When deletion occurred
    """

    event_type: str = "DocumentDeleted"
    aggregate_type: str = "UploadedDocument"

    document_id: UUID = Field(description="Document ID")
    deleted_by_user_id: UUID = Field(description="User who deleted")
    cascade_entities: bool = Field(
        description="Whether entities were cascaded",
        default=False,
    )
    entity_count_deleted: int = Field(
        description="Entities deleted if cascaded",
        ge=0,
        default=0,
    )
    deleted_at: datetime = Field(description="When deletion occurred")


__all__ = [
    "DocumentDeleted",
    "DocumentExtractionCompleted",
    "DocumentExtractionFailed",
    "DocumentExtractionStarted",
    "DocumentParsingCompleted",
    "DocumentParsingFailed",
    "DocumentParsingStarted",
    "DocumentUploaded",
]
