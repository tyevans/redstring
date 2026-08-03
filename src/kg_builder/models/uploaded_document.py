"""
SQLAlchemy model for uploaded documents.

Documents are files uploaded to projects for knowledge extraction.
Supports TXT, Markdown, and HTML content types.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kg_builder.db import Base


class DocumentContentType(str, enum.Enum):
    """Allowed content types for document uploads."""

    TEXT_PLAIN = "text/plain"
    TEXT_MARKDOWN = "text/markdown"
    TEXT_HTML = "text/html"


class DocumentExtractionStatus(str, enum.Enum):
    """Status of document extraction process."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class UploadedDocument(Base):
    """
    Model representing an uploaded document for extraction.

    Documents are uploaded to projects and go through the same extraction
    pipeline as scraped web pages. They support temporal extraction for
    timeline views.

    Attributes:
        id: Unique document identifier
        tenant_id: Tenant for multi-tenancy isolation (RLS enforced)
        project_id: Project this document belongs to
        created_by_user_id: User who uploaded the document
        filename: Original filename
        content_type: MIME type (text/plain, text/markdown, text/html)
        file_size_bytes: Size of the uploaded file
        storage_bucket: MinIO bucket where file is stored
        storage_key: MinIO object key (path)
        extraction_status: Current extraction status
        extraction_error: Error message if extraction failed
        enable_timeline_extraction: Whether to extract temporal data
        entity_count: Number of entities extracted from document
        created_at: When the document was uploaded
        updated_at: Last update timestamp
    """

    __tablename__ = "uploaded_documents"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Multi-tenancy (RLS enforced)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Project relationship
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # User who uploaded
    created_by_user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # File metadata
    filename: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    content_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    file_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    # MinIO storage location
    storage_bucket: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    storage_key: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )

    # Extraction status
    extraction_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DocumentExtractionStatus.PENDING.value,
    )
    extraction_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Timeline extraction flag
    enable_timeline_extraction: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
    )

    # Extraction results
    entity_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Indexes
    __table_args__ = (
        # Index for listing documents by project
        Index("ix_uploaded_documents_project_id_created_at", "project_id", "created_at"),
        # Index for listing documents by status
        Index("ix_uploaded_documents_extraction_status", "extraction_status"),
        # Index for tenant + project queries
        Index("ix_uploaded_documents_tenant_project", "tenant_id", "project_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<UploadedDocument(id={self.id}, filename='{self.filename}', "
            f"status={self.extraction_status})>"
        )
