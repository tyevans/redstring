"""
Scraping job model for web scraping orchestration.

This module defines the ScrapingJob model, which represents a web scraping job
configuration and its execution status. Jobs are tenant-isolated via RLS policies.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Type alias for extraction strategy values
ExtractionStrategyType = Literal["legacy", "auto_detect", "manual"]

from kg_builder.db import Base

if TYPE_CHECKING:
    from kg_builder.models.extraction_provider import ExtractionProvider
    from kg_builder.models.scraped_page import ScrapedPage
    from kg_builder.models.tenant import Tenant


class JobStatus(str, enum.Enum):
    """Enumeration of scraping job states."""

    PENDING = "pending"  # Created but not started
    QUEUED = "queued"  # Submitted to Celery queue
    RUNNING = "running"  # Spider is actively crawling
    PAUSED = "paused"  # Temporarily stopped (can resume)
    COMPLETED = "completed"  # Finished successfully
    FAILED = "failed"  # Terminated with error
    CANCELLED = "cancelled"  # User-cancelled


class JobStage(str, enum.Enum):
    """Enumeration of scraping job pipeline stages."""

    CRAWLING = "crawling"  # Spider is actively fetching pages
    EXTRACTING = "extracting"  # Entity extraction in progress
    CONSOLIDATING = "consolidating"  # Entity consolidation running
    DONE = "done"  # All stages complete


class ScrapingJob(Base):
    """
    Represents a web scraping job configuration and status.

    Each job belongs to a tenant and contains configuration for how to crawl
    a website, including start URL, depth, speed limits, and more. The job
    tracks progress and can be started, stopped, or cancelled.

    Attributes:
        id: UUID primary key for security
        tenant_id: Foreign key to tenant (RLS enforced)
        created_by_user_id: User who created the job
        name: Human-readable job name
        start_url: URL to begin crawling from
        allowed_domains: List of domains to stay within
        url_patterns: Regex patterns for URLs to include
        excluded_patterns: Regex patterns for URLs to exclude
        crawl_depth: Maximum link depth to follow
        max_pages: Maximum pages to scrape
        crawl_speed: Requests per second limit
        respect_robots_txt: Honor robots.txt rules
        custom_settings: Additional Scrapy settings
        extraction_strategy: Extraction approach (legacy, auto_detect, manual)
        content_domain: Detected or selected domain ID
        classification_confidence: Confidence score from auto-detection
        inferred_schema: Snapshot of domain schema for extraction
        classification_sample_size: Pages to sample for classification (1-5)
        status: Current job status
        celery_task_id: Celery task ID for control
        pages_crawled: Number of pages successfully scraped
        entities_extracted: Number of entities found
        errors_count: Number of errors encountered
        started_at: When crawling began
        completed_at: When crawling finished
        error_message: Last error message if failed
        created_at: Timestamp of creation (inherited)
        updated_at: Timestamp of last update (inherited)

    Properties:
        uses_adaptive_extraction: True if using auto_detect or manual strategy
        needs_classification: True if classification is still needed
        is_domain_resolved: True if domain is set or not needed (legacy)
    """

    __tablename__ = "scraping_jobs"

    # Table-level constraints for adaptive extraction strategy
    __table_args__ = (
        CheckConstraint(
            "extraction_strategy IN ('legacy', 'auto_detect', 'manual')",
            name="ck_scraping_jobs_extraction_strategy",
        ),
        CheckConstraint(
            "classification_sample_size >= 1 AND classification_sample_size <= 5",
            name="ck_scraping_jobs_classification_sample_size",
        ),
        CheckConstraint(
            "classification_confidence IS NULL OR (classification_confidence >= 0.0 AND classification_confidence <= 1.0)",
            name="ck_scraping_jobs_classification_confidence",
        ),
    )

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
        comment="Tenant this job belongs to (RLS enforced)",
    )

    # Job ownership
    created_by_user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="User ID who created this job",
    )

    # Job configuration
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Human-readable job name",
    )

    start_url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
        comment="URL to begin crawling from",
    )

    allowed_domains: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        insert_default=list,
        comment="List of domains to stay within",
    )

    url_patterns: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Regex patterns for URLs to include",
    )

    excluded_patterns: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Regex patterns for URLs to exclude",
    )

    crawl_depth: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=2,
        insert_default=2,
        comment="Maximum link depth to follow",
    )

    max_pages: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        insert_default=100,
        comment="Maximum number of pages to scrape",
    )

    crawl_speed: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        insert_default=1.0,
        comment="Requests per second limit",
    )

    respect_robots_txt: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        insert_default=True,
        comment="Honor robots.txt rules",
    )

    use_llm_extraction: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        insert_default=True,
        comment="Use LLM for semantic entity extraction",
    )

    extraction_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extraction_providers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Provider to use for extraction (null = use default/global)",
    )

    custom_settings: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        insert_default=dict,
        comment="Additional Scrapy settings",
    )

    # Adaptive Extraction Strategy columns
    extraction_strategy: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="legacy",
        server_default="legacy",
        index=True,
        comment="Extraction strategy: legacy, auto_detect, or manual",
    )

    content_domain: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        index=True,
        comment="Content domain ID (e.g., literature_fiction)",
    )

    classification_confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Classification confidence score (0.0-1.0)",
    )

    inferred_schema: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Snapshot of domain schema used for extraction",
    )

    classification_sample_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        comment="Number of pages to sample for classification (1-5)",
    )

    # Status tracking
    status: Mapped[JobStatus] = mapped_column(
        SQLEnum(
            JobStatus,
            name="job_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=JobStatus.PENDING,
        insert_default=JobStatus.PENDING,
        index=True,
        comment="Current job status",
    )

    celery_task_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Celery task ID for job control",
    )

    # Stage tracking
    stage: Mapped[JobStage] = mapped_column(
        SQLEnum(
            JobStage,
            name="job_stage",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=JobStage.CRAWLING,
        insert_default=JobStage.CRAWLING,
        index=True,
        comment="Current stage within the job lifecycle",
    )

    consolidation_task_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Celery task ID for consolidation job",
    )

    # Stage progress tracking
    extraction_progress: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        insert_default=0.0,
        comment="Extraction progress (0.0-1.0)",
    )

    consolidation_progress: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        insert_default=0.0,
        comment="Consolidation progress (0.0-1.0)",
    )

    pages_pending_extraction: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        insert_default=0,
        comment="Pages awaiting entity extraction",
    )

    consolidation_candidates_found: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        insert_default=0,
        comment="Number of merge candidates identified",
    )

    consolidation_auto_merged: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        insert_default=0,
        comment="Number of auto-merged entity pairs",
    )

    # Progress metrics
    pages_crawled: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        insert_default=0,
        comment="Number of pages successfully scraped",
    )

    entities_extracted: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        insert_default=0,
        comment="Number of entities extracted",
    )

    errors_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        insert_default=0,
        comment="Number of errors encountered",
    )

    # Timing
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When crawling began",
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When crawling finished",
    )

    # Error handling
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Last error message if failed",
    )

    # Relationships
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        doc="Tenant this job belongs to",
    )

    scraped_pages: Mapped[list[ScrapedPage]] = relationship(
        "ScrapedPage",
        back_populates="job",
        cascade="all, delete-orphan",
        doc="Pages scraped by this job",
    )

    extraction_provider: Mapped[ExtractionProvider | None] = relationship(
        "ExtractionProvider",
        back_populates="scraping_jobs",
        doc="Extraction provider to use for this job",
    )

    def __init__(self, **kwargs):
        """Initialize job with default values for optional fields."""
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        if "allowed_domains" not in kwargs:
            kwargs["allowed_domains"] = []
        if "custom_settings" not in kwargs:
            kwargs["custom_settings"] = {}
        if "status" not in kwargs:
            kwargs["status"] = JobStatus.PENDING
        if "stage" not in kwargs:
            kwargs["stage"] = JobStage.CRAWLING
        # Adaptive extraction defaults
        if "extraction_strategy" not in kwargs:
            kwargs["extraction_strategy"] = "legacy"
        if "classification_sample_size" not in kwargs:
            kwargs["classification_sample_size"] = 1
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        """Return string representation of the job."""
        return f"<ScrapingJob {self.id} '{self.name}' ({self.status.value}, {self.stage.value})>"

    @property
    def is_active(self) -> bool:
        """Check if job is currently active (queued or running)."""
        return self.status in (JobStatus.QUEUED, JobStatus.RUNNING)

    @property
    def can_start(self) -> bool:
        """Check if job can be started."""
        return self.status in (JobStatus.PENDING, JobStatus.PAUSED)

    @property
    def can_stop(self) -> bool:
        """Check if job can be stopped."""
        return self.status in (JobStatus.QUEUED, JobStatus.RUNNING)

    @property
    def uses_adaptive_extraction(self) -> bool:
        """Check if this job uses adaptive extraction.

        Adaptive extraction is used when the extraction strategy is either
        'auto_detect' (domain determined by content classification) or
        'manual' (domain specified by user).

        Returns:
            True if using adaptive extraction, False for legacy extraction.
        """
        return self.extraction_strategy in ("auto_detect", "manual")

    @property
    def needs_classification(self) -> bool:
        """Check if this job needs content classification.

        Classification is needed when using auto_detect strategy and
        the content domain has not yet been determined.

        Returns:
            True if classification is needed, False otherwise.
        """
        return self.extraction_strategy == "auto_detect" and self.content_domain is None

    @property
    def is_domain_resolved(self) -> bool:
        """Check if the content domain has been determined.

        For legacy strategy, this always returns True since domains are not used.
        For adaptive strategies, returns True when content_domain is set.

        Returns:
            True if domain is resolved or not needed, False if still pending.
        """
        if self.extraction_strategy == "legacy":
            return True  # Legacy doesn't use domains
        return self.content_domain is not None
