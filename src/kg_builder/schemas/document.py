"""
Pydantic schemas for document upload and management.

Documents are files uploaded to projects for knowledge extraction.
Supports TXT, Markdown, and HTML content types.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class DocumentContentTypeSchema(str, Enum):
    """Allowed content types for document uploads."""

    TEXT_PLAIN = "text/plain"
    TEXT_MARKDOWN = "text/markdown"
    TEXT_HTML = "text/html"


class DocumentExtractionStatusSchema(str, Enum):
    """Status of document extraction process."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# =============================================================================
# Request Schemas
# =============================================================================


class UploadDocumentOptions(BaseModel):
    """Options for document upload."""

    enable_timeline_extraction: bool = Field(
        default=True,
        description="Whether to extract temporal data for timeline views",
    )


# =============================================================================
# Response Schemas
# =============================================================================


class UploadedDocumentResponse(BaseModel):
    """Response for uploaded document."""

    id: UUID = Field(..., description="Document ID")
    project_id: UUID = Field(..., description="Project this document belongs to")
    filename: str = Field(..., description="Original filename")
    content_type: str = Field(..., description="MIME type (text/plain, text/markdown, text/html)")
    file_size_bytes: int = Field(..., description="Size of the file in bytes")
    extraction_status: str = Field(..., description="Extraction status (pending, processing, completed, failed)")
    enable_timeline_extraction: bool = Field(..., description="Whether timeline extraction is enabled")
    entity_count: int = Field(..., description="Number of entities extracted")
    created_at: datetime = Field(..., description="When the document was uploaded")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


class UploadedDocumentDetail(UploadedDocumentResponse):
    """Detailed response for uploaded document including storage info."""

    extraction_error: str | None = Field(
        None,
        description="Error message if extraction failed",
    )
    storage_key: str = Field(..., description="MinIO/S3 object key")
    download_url: str | None = Field(
        None,
        description="Presigned URL for downloading (if available)",
    )
    created_by_user_id: str = Field(..., description="User who uploaded the document")


class PaginatedDocumentResponse(BaseModel):
    """Paginated list of documents."""

    items: list[UploadedDocumentResponse] = Field(
        ...,
        description="List of documents",
    )
    total: int = Field(..., description="Total number of documents")
    limit: int = Field(..., description="Maximum items per page")
    offset: int = Field(..., description="Items skipped")
    has_more: bool = Field(..., description="Whether more items are available")


class DocumentUploadResponse(BaseModel):
    """Response for successful document upload."""

    id: UUID = Field(..., description="Document ID")
    filename: str = Field(..., description="Original filename")
    content_type: str = Field(..., description="MIME type")
    file_size_bytes: int = Field(..., description="File size in bytes")
    extraction_status: str = Field(..., description="Initial extraction status")
    message: str = Field(
        default="Document uploaded successfully. Extraction will begin shortly.",
        description="Status message",
    )


class DocumentDeleteResponse(BaseModel):
    """Response for document deletion."""

    id: UUID = Field(..., description="Deleted document ID")
    message: str = Field(
        default="Document deleted successfully",
        description="Status message",
    )


class DocumentReprocessResponse(BaseModel):
    """Response for document reprocessing request."""

    id: UUID = Field(..., description="Document ID")
    extraction_status: str = Field(
        default="pending",
        description="New extraction status",
    )
    message: str = Field(
        default="Document queued for reprocessing",
        description="Status message",
    )
