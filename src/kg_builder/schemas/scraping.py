"""
Pydantic schemas for web scraping API.

This module provides Pydantic models for scraping job management,
including request validation and response serialization.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, model_validator

from kg_builder.models.extracted_entity import ExtractionMethod
from kg_builder.models.scraping_job import JobStage, JobStatus

# Type alias for extraction strategy
ExtractionStrategy = Literal["legacy", "auto_detect", "manual"]

# Note: EntityType enum is no longer used in schemas since entity_type is now
# stored as a string to support dynamic domain-specific types.


# =============================================================================
# Scraping Job Schemas
# =============================================================================


class CreateScrapingJobRequest(BaseModel):
    """Request schema for creating a new scraping job.

    Supports both legacy extraction (existing LLM extraction) and adaptive
    extraction strategies (auto_detect or manual domain selection).
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable job name",
    )
    start_url: HttpUrl = Field(
        ...,
        description="URL to begin crawling from",
    )
    allowed_domains: list[str] = Field(
        default_factory=list,
        description="List of domains to stay within (empty = derived from start_url)",
    )
    url_patterns: list[str] | None = Field(
        None,
        description="Regex patterns for URLs to include",
    )
    excluded_patterns: list[str] | None = Field(
        None,
        description="Regex patterns for URLs to exclude",
    )
    crawl_depth: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Maximum link depth to follow",
    )
    max_pages: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Maximum number of pages to scrape",
    )
    crawl_speed: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Requests per second limit",
    )
    respect_robots_txt: bool = Field(
        default=True,
        description="Honor robots.txt rules",
    )
    use_llm_extraction: bool = Field(
        default=True,
        description="Use LLM for semantic entity extraction",
    )
    extraction_provider_id: UUID | None = Field(
        None,
        description="Extraction provider to use (null = use tenant default or global)",
    )
    custom_settings: dict = Field(
        default_factory=dict,
        description="Additional Scrapy settings",
    )

    # Adaptive extraction fields
    extraction_strategy: ExtractionStrategy = Field(
        default="legacy",
        description="Extraction strategy: legacy (existing behavior), auto_detect (classify content), or manual (user-specified domain)",
    )
    content_domain: str | None = Field(
        default=None,
        max_length=50,
        description="Content domain ID (required for manual strategy, e.g., 'literature_fiction')",
    )
    classification_sample_size: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Number of pages to sample for classification (only for auto_detect, 1-5)",
    )

    @model_validator(mode="after")
    def validate_extraction_strategy(self) -> "CreateScrapingJobRequest":
        """Validate content_domain based on extraction_strategy."""
        if self.extraction_strategy == "manual" and self.content_domain is None:
            raise ValueError("content_domain is required when extraction_strategy is 'manual'")

        if self.extraction_strategy == "legacy" and self.content_domain is not None:
            raise ValueError(
                "content_domain should not be set when extraction_strategy is 'legacy'"
            )

        # Reset sample size to default if not using auto_detect
        if self.extraction_strategy != "auto_detect" and self.classification_sample_size != 1:
            # Note: We allow this but it will be ignored
            pass

        return self


class UpdateScrapingJobRequest(BaseModel):
    """Request schema for updating a scraping job (before it starts).

    Note: extraction_strategy and content_domain cannot be changed after job
    creation to maintain data consistency. These fields are intentionally
    not included in this schema.
    """

    name: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="Human-readable job name",
    )
    crawl_depth: int | None = Field(
        None,
        ge=1,
        le=10,
        description="Maximum link depth to follow",
    )
    max_pages: int | None = Field(
        None,
        ge=1,
        le=10000,
        description="Maximum number of pages to scrape",
    )
    crawl_speed: float | None = Field(
        None,
        ge=0.1,
        le=10.0,
        description="Requests per second limit",
    )
    use_llm_extraction: bool | None = Field(
        None,
        description="Use LLM for semantic entity extraction",
    )
    # Note: extraction_strategy, content_domain, and classification_sample_size
    # are intentionally excluded - they are immutable after job creation


class ScrapingJobSummary(BaseModel):
    """Summary view of a scraping job."""

    id: UUID = Field(..., description="Job ID")
    name: str = Field(..., description="Job name")
    start_url: str = Field(..., description="Starting URL")
    status: JobStatus = Field(..., description="Current status")
    stage: JobStage | None = Field(None, description="Current pipeline stage")
    pages_crawled: int = Field(..., description="Pages scraped so far")
    entities_extracted: int = Field(..., description="Entities found so far")

    # Adaptive extraction summary fields
    extraction_strategy: str = Field(
        default="legacy",
        description="Extraction strategy being used",
    )
    content_domain: str | None = Field(
        default=None,
        description="Content domain ID (if using adaptive extraction)",
    )
    uses_adaptive_extraction: bool = Field(
        default=False,
        description="Whether adaptive extraction is enabled",
    )

    created_at: datetime = Field(..., description="Creation timestamp")

    class Config:
        from_attributes = True


class ScrapingJobResponse(BaseModel):
    """Full scraping job response.

    Includes all job configuration, status, progress metrics, and
    adaptive extraction strategy information.
    """

    id: UUID = Field(..., description="Job ID")
    tenant_id: UUID = Field(..., description="Tenant ID")
    created_by_user_id: str = Field(..., description="Creator user ID")
    name: str = Field(..., description="Job name")
    start_url: str = Field(..., description="Starting URL")
    allowed_domains: list[str] = Field(..., description="Allowed domains")
    url_patterns: list[str] | None = Field(None, description="URL include patterns")
    excluded_patterns: list[str] | None = Field(None, description="URL exclude patterns")
    crawl_depth: int = Field(..., description="Max crawl depth")
    max_pages: int = Field(..., description="Max pages to scrape")
    crawl_speed: float = Field(..., description="Requests per second")
    respect_robots_txt: bool = Field(..., description="Honor robots.txt")
    use_llm_extraction: bool = Field(..., description="Use LLM extraction")
    extraction_provider_id: UUID | None = Field(None, description="Extraction provider ID")
    custom_settings: dict = Field(..., description="Custom Scrapy settings")

    # Adaptive extraction fields
    extraction_strategy: str = Field(
        default="legacy",
        description="Extraction strategy: legacy, auto_detect, or manual",
    )
    content_domain: str | None = Field(
        default=None,
        description="Content domain ID (e.g., 'literature_fiction')",
    )
    classification_confidence: float | None = Field(
        default=None,
        description="Classification confidence score (0.0-1.0, for auto_detect)",
    )
    classification_sample_size: int = Field(
        default=1,
        description="Pages sampled for classification",
    )
    uses_adaptive_extraction: bool = Field(
        default=False,
        description="Whether adaptive extraction is enabled (auto_detect or manual)",
    )
    is_domain_resolved: bool = Field(
        default=True,
        description="Whether the content domain has been determined",
    )

    # Status and progress
    status: JobStatus = Field(..., description="Current status")
    stage: JobStage | None = Field(None, description="Current pipeline stage")
    celery_task_id: str | None = Field(None, description="Celery task ID")
    consolidation_task_id: str | None = Field(None, description="Consolidation task ID")
    pages_crawled: int = Field(..., description="Pages scraped")
    entities_extracted: int = Field(..., description="Entities extracted")
    errors_count: int = Field(..., description="Error count")
    extraction_progress: float = Field(0.0, description="Extraction progress (0.0-1.0)")
    consolidation_progress: float = Field(0.0, description="Consolidation progress (0.0-1.0)")
    pages_pending_extraction: int = Field(0, description="Pages pending extraction")
    consolidation_candidates_found: int = Field(0, description="Merge candidates found")
    consolidation_auto_merged: int = Field(0, description="Auto-merged entity pairs")
    started_at: datetime | None = Field(None, description="Start time")
    completed_at: datetime | None = Field(None, description="Completion time")
    error_message: str | None = Field(None, description="Error message")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


class JobStatusResponse(BaseModel):
    """Job status and progress response."""

    job_id: UUID = Field(..., description="Job ID")
    status: JobStatus = Field(..., description="Current status")
    stage: JobStage | None = Field(None, description="Current pipeline stage")
    pages_crawled: int = Field(..., description="Pages scraped")
    entities_extracted: int = Field(..., description="Entities extracted")
    errors_count: int = Field(..., description="Error count")
    started_at: datetime | None = Field(None, description="Start time")
    completed_at: datetime | None = Field(None, description="Completion time")
    error_message: str | None = Field(None, description="Error message if failed")
    estimated_progress: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Estimated overall progress (0.0-1.0)",
    )
    # Stage-specific progress
    crawl_progress: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Crawling progress (0.0-1.0)",
    )
    extraction_progress: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Extraction progress (0.0-1.0)",
    )
    consolidation_progress: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Consolidation progress (0.0-1.0)",
    )
    # Consolidation metrics
    consolidation_candidates_found: int = Field(0, description="Merge candidates found")
    consolidation_auto_merged: int = Field(0, description="Auto-merged entity pairs")
    pages_pending_extraction: int = Field(0, description="Pages pending extraction")


# =============================================================================
# Scraped Page Schemas
# =============================================================================


class ScrapedPageSummary(BaseModel):
    """Summary view of a scraped page."""

    id: UUID = Field(..., description="Page ID")
    url: str = Field(..., description="Page URL")
    title: str | None = Field(None, description="Page title")
    http_status: int = Field(..., description="HTTP status code")
    depth: int = Field(..., description="Link depth")
    extraction_status: str = Field(..., description="Extraction status")
    crawled_at: datetime = Field(..., description="Crawl timestamp")

    class Config:
        from_attributes = True


class ScrapedPageDetail(BaseModel):
    """Detailed view of a scraped page."""

    id: UUID = Field(..., description="Page ID")
    job_id: UUID = Field(..., description="Parent job ID")
    url: str = Field(..., description="Page URL")
    canonical_url: str | None = Field(None, description="Canonical URL")
    title: str | None = Field(None, description="Page title")
    meta_description: str | None = Field(None, description="Meta description")
    meta_keywords: str | None = Field(None, description="Meta keywords")
    http_status: int = Field(..., description="HTTP status code")
    content_type: str = Field(..., description="Content-Type")
    depth: int = Field(..., description="Link depth")
    crawled_at: datetime = Field(..., description="Crawl timestamp")
    extraction_status: str = Field(..., description="Extraction status")
    extracted_at: datetime | None = Field(None, description="Extraction timestamp")
    extraction_error: str | None = Field(None, description="Extraction error")
    schema_org_count: int = Field(
        default=0,
        description="Number of Schema.org items found",
    )
    entity_count: int = Field(
        default=0,
        description="Number of entities extracted",
    )
    created_at: datetime = Field(..., description="Creation timestamp")

    class Config:
        from_attributes = True


class ScrapedPageContent(BaseModel):
    """Full content of a scraped page."""

    id: UUID = Field(..., description="Page ID")
    url: str = Field(..., description="Page URL")
    html_content: str = Field(..., description="Raw HTML content")
    text_content: str = Field(..., description="Extracted text content")
    schema_org_data: list = Field(..., description="Schema.org JSON-LD data")
    open_graph_data: dict = Field(..., description="Open Graph metadata")
    response_headers: dict = Field(..., description="HTTP response headers")

    class Config:
        from_attributes = True


# =============================================================================
# Extracted Entity Schemas
# =============================================================================


class ExtractedEntitySummary(BaseModel):
    """Summary view of an extracted entity."""

    id: UUID = Field(..., description="Entity ID")
    entity_type: str = Field(
        ..., description="Entity type (e.g., 'person', 'organization', 'character')"
    )
    name: str = Field(..., description="Entity name")
    extraction_method: ExtractionMethod = Field(..., description="Extraction method")
    confidence_score: float = Field(..., description="Confidence score")
    created_at: datetime = Field(..., description="Creation timestamp")

    class Config:
        from_attributes = True


class ExtractedEntityDetail(BaseModel):
    """Detailed view of an extracted entity."""

    id: UUID = Field(..., description="Entity ID")
    tenant_id: UUID = Field(..., description="Tenant ID")
    source_page_id: UUID = Field(..., description="Source page ID")
    entity_type: str = Field(
        ..., description="Entity type (e.g., 'person', 'organization', 'character')"
    )
    original_entity_type: str | None = Field(
        None, description="Original entity type from LLM before normalization"
    )
    name: str = Field(..., description="Entity name")
    normalized_name: str = Field(..., description="Normalized name")
    description: str | None = Field(None, description="Entity description")
    external_ids: dict = Field(..., description="External identifiers")
    properties: dict = Field(..., description="Entity properties")
    extraction_method: ExtractionMethod = Field(..., description="Extraction method")
    confidence_score: float = Field(..., description="Confidence score")
    source_text: str | None = Field(None, description="Source text snippet")
    neo4j_node_id: str | None = Field(None, description="Neo4j node ID")
    synced_to_neo4j: bool = Field(..., description="Neo4j sync status")
    synced_at: datetime | None = Field(None, description="Neo4j sync timestamp")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


class EntityRelationshipResponse(BaseModel):
    """Response for an entity relationship."""

    id: UUID = Field(..., description="Relationship ID")
    source_entity_id: UUID = Field(..., description="Source entity ID")
    target_entity_id: UUID = Field(..., description="Target entity ID")
    relationship_type: str = Field(..., description="Relationship type")
    properties: dict = Field(..., description="Relationship properties")
    confidence_score: float = Field(..., description="Confidence score")
    synced_to_neo4j: bool = Field(..., description="Neo4j sync status")
    created_at: datetime = Field(..., description="Creation timestamp")

    # Expanded entity info (optional)
    source_entity_name: str | None = Field(None, description="Source entity name")
    source_entity_type: str | None = Field(None, description="Source entity type")
    target_entity_name: str | None = Field(None, description="Target entity name")
    target_entity_type: str | None = Field(None, description="Target entity type")

    class Config:
        from_attributes = True


# =============================================================================
# Paginated Response Schema
# =============================================================================


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""

    items: list = Field(..., description="List of items")
    total: int = Field(..., description="Total number of items")
    page: int = Field(..., description="Current page (1-indexed)")
    page_size: int = Field(..., description="Items per page")
    pages: int = Field(..., description="Total number of pages")
    has_next: bool = Field(..., description="Whether there's a next page")
    has_prev: bool = Field(..., description="Whether there's a previous page")


# =============================================================================
# Knowledge Graph Query Schemas
# =============================================================================


class GraphQueryRequest(BaseModel):
    """Request for querying the knowledge graph."""

    entity_id: UUID = Field(..., description="Central entity ID")
    depth: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Relationship depth to traverse",
    )
    relationship_types: list[str] | None = Field(
        None,
        description="Filter by relationship types",
    )
    entity_types: list[str] | None = Field(
        None,
        description="Filter by entity types (e.g., ['person', 'organization', 'character'])",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum nodes to return",
    )


class GraphNode(BaseModel):
    """A node in the knowledge graph response."""

    id: UUID = Field(..., description="Entity ID")
    entity_type: str = Field(
        ..., description="Entity type (e.g., 'person', 'organization', 'character')"
    )
    name: str = Field(..., description="Entity name")
    properties: dict = Field(default_factory=dict, description="Entity properties")


class GraphEdge(BaseModel):
    """An edge in the knowledge graph response."""

    source: UUID = Field(..., description="Source entity ID")
    target: UUID = Field(..., description="Target entity ID")
    relationship_type: str = Field(..., description="Relationship type")
    confidence: float = Field(..., description="Confidence score")


class GraphQueryResponse(BaseModel):
    """Response from a knowledge graph query."""

    nodes: list[GraphNode] = Field(..., description="Graph nodes")
    edges: list[GraphEdge] = Field(..., description="Graph edges")
    total_nodes: int = Field(..., description="Total nodes found")
    total_edges: int = Field(..., description="Total edges found")
    truncated: bool = Field(
        default=False,
        description="Whether results were truncated",
    )
