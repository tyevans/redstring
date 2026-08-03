"""
Scraped page model for storing crawled web content.

This module defines the ScrapedPage model, which stores the raw content
and metadata from scraped web pages. Pages are tenant-isolated via RLS.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kg_builder.db import Base

if TYPE_CHECKING:
    from kg_builder.models.extracted_entity import ExtractedEntity
    from kg_builder.models.scraping_job import ScrapingJob
    from kg_builder.models.tenant import Tenant


class ScrapedPage(Base):
    """
    Represents a scraped web page and its content.

    Stores the raw HTML, extracted text, and structured data from a web page.
    Each page belongs to a scraping job and tenant.

    Attributes:
        id: UUID primary key for security
        tenant_id: Foreign key to tenant (RLS enforced)
        job_id: Foreign key to parent scraping job
        url: Full URL of the scraped page
        canonical_url: Canonical URL if different from url
        content_hash: SHA-256 hash of HTML for deduplication
        html_content: Raw HTML content
        text_content: Extracted text content (stripped of HTML)
        title: Page title from <title> tag
        meta_description: Meta description content
        schema_org_data: Extracted JSON-LD/Schema.org data
        open_graph_data: Extracted Open Graph metadata
        http_status: HTTP response status code
        content_type: HTTP Content-Type header value
        crawled_at: When the page was crawled
        depth: Link depth from start URL
        extraction_status: Entity extraction status
        extracted_at: When entity extraction completed
        created_at: Timestamp of creation (inherited)
        updated_at: Timestamp of last update (inherited)
    """

    __tablename__ = "scraped_pages"

    # Primary key - UUID for security
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        insert_default=uuid.uuid4,
        index=True,
        comment="UUID primary key for security",
    )

    # Tenant isolation (RLS enforced)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Tenant this page belongs to (RLS enforced)",
    )

    # Parent job reference
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scraping_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Scraping job that crawled this page",
    )

    # Page identification
    url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
        index=True,
        comment="Full URL of the scraped page",
    )

    canonical_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
        comment="Canonical URL if different from url",
    )

    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="SHA-256 hash of HTML for deduplication",
    )

    # Raw content
    html_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Raw HTML content",
    )

    text_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Extracted text content (stripped of HTML)",
    )

    # Extracted metadata
    title: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="Page title from <title> tag",
    )

    meta_description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Meta description content",
    )

    meta_keywords: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Meta keywords content",
    )

    # Structured data
    schema_org_data: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        insert_default=list,
        comment="Extracted JSON-LD/Schema.org data",
    )

    open_graph_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        insert_default=dict,
        comment="Extracted Open Graph metadata",
    )

    # HTTP response metadata
    http_status: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="HTTP response status code",
    )

    content_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="text/html",
        insert_default="text/html",
        comment="HTTP Content-Type header value",
    )

    response_headers: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        insert_default=dict,
        comment="HTTP response headers",
    )

    # Crawl metadata
    crawled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When the page was crawled",
    )

    depth: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        insert_default=0,
        comment="Link depth from start URL",
    )

    # Extraction status
    extraction_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="pending",
        insert_default="pending",
        index=True,
        comment="Entity extraction status: pending, processing, completed, failed",
    )

    extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When entity extraction completed",
    )

    extraction_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if extraction failed",
    )

    # Relationships
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        doc="Tenant this page belongs to",
    )

    job: Mapped[ScrapingJob] = relationship(
        "ScrapingJob",
        back_populates="scraped_pages",
        doc="Job that scraped this page",
    )

    entities: Mapped[list[ExtractedEntity]] = relationship(
        "ExtractedEntity",
        back_populates="source_page",
        cascade="all, delete-orphan",
        doc="Entities extracted from this page",
    )

    def __init__(self, **kwargs):
        """Initialize page with default values for optional fields."""
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        if "schema_org_data" not in kwargs:
            kwargs["schema_org_data"] = []
        if "open_graph_data" not in kwargs:
            kwargs["open_graph_data"] = {}
        if "response_headers" not in kwargs:
            kwargs["response_headers"] = {}
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        """Return string representation of the page."""
        return f"<ScrapedPage {self.id} '{self.url[:50]}...'>"

    @property
    def domain(self) -> str:
        """Extract domain from URL."""
        from urllib.parse import urlparse

        parsed = urlparse(self.url)
        return parsed.netloc
