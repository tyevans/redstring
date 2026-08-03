"""
Pydantic schemas for project API endpoints.

These schemas define the request/response models for project CRUD operations,
statistics, and job management within projects.

Schema naming conventions:
- *Request: Input models for API endpoints
- *Response: Output models with computed/derived fields
- *Summary: Condensed view for list endpoints
- *Detail: Full view with all fields

All schemas use Pydantic v2 patterns with Field() for validation and documentation.
"""

from datetime import datetime
from typing import Annotated, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================================================================
# Settings Schema
# =============================================================================


class ProjectSettingsSchema(BaseModel):
    """
    Schema for project settings (extraction defaults).

    These settings provide defaults for jobs created within the project.
    All fields are optional - unset fields use system defaults.

    Attributes:
        default_extraction_provider_id: UUID of default extraction provider
        default_extraction_strategy: Strategy for extraction
        default_content_domain: Content domain for manual strategy
        enable_timeline_extraction: Whether to extract timeline data
    """

    model_config = ConfigDict(extra="forbid")

    default_extraction_provider_id: Optional[UUID] = Field(
        default=None,
        description="Default extraction provider for new jobs",
        json_schema_extra={"example": "550e8400-e29b-41d4-a716-446655440000"},
    )
    default_extraction_strategy: Optional[str] = Field(
        default=None,
        description="Default extraction strategy: legacy, auto_detect, manual",
        json_schema_extra={"example": "auto_detect"},
    )
    default_content_domain: Optional[str] = Field(
        default=None,
        description="Default content domain for manual strategy",
        max_length=100,
        json_schema_extra={"example": "literature_fiction"},
    )
    enable_timeline_extraction: Optional[bool] = Field(
        default=None,
        description="Whether to extract timeline data by default",
        json_schema_extra={"example": True},
    )

    @field_validator("default_extraction_strategy")
    @classmethod
    def validate_extraction_strategy(cls, v: Optional[str]) -> Optional[str]:
        """Validate that extraction strategy is one of the allowed values."""
        if v is not None and v not in ("legacy", "auto_detect", "manual"):
            raise ValueError(
                f"Invalid extraction strategy '{v}'. "
                "Must be one of: legacy, auto_detect, manual"
            )
        return v


# =============================================================================
# Request Schemas
# =============================================================================


class CreateProjectRequest(BaseModel):
    """
    Request schema for creating a new project.

    Attributes:
        name: Human-readable project name (required)
        description: Optional project description
        settings: Optional initial settings (extraction defaults)
        tags: Optional tags for categorization
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=255,
            description="Human-readable project name",
            json_schema_extra={"example": "Climate Research"},
        ),
    ]
    description: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Optional project description",
        json_schema_extra={"example": "Research on climate change data sources"},
    )
    settings: Optional[ProjectSettingsSchema] = Field(
        default=None,
        description="Optional initial settings (extraction defaults)",
    )
    tags: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Optional tags for categorization",
        json_schema_extra={"example": ["research", "climate", "priority"]},
    )

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        """Validate individual tag lengths."""
        for tag in v:
            if len(tag) > 50:
                raise ValueError(
                    f"Tag '{tag[:20]}...' exceeds maximum length of 50 characters"
                )
            if len(tag) < 1:
                raise ValueError("Tags cannot be empty strings")
        return v


class UpdateProjectRequest(BaseModel):
    """
    Request schema for updating project metadata.

    All fields are optional - only provided fields are updated.
    Settings updates should use the dedicated settings endpoint.

    Attributes:
        name: New project name (optional)
        description: New description (optional)
        tags: New tags (optional, replaces all tags)
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New project name",
        json_schema_extra={"example": "Updated Project Name"},
    )
    description: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="New project description",
        json_schema_extra={"example": "Updated description for the project"},
    )
    tags: Optional[list[str]] = Field(
        default=None,
        max_length=20,
        description="New tags (replaces all existing tags)",
        json_schema_extra={"example": ["updated", "tags"]},
    )

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        """Validate individual tag lengths if provided."""
        if v is not None:
            for tag in v:
                if len(tag) > 50:
                    raise ValueError(
                        f"Tag '{tag[:20]}...' exceeds maximum length of 50 characters"
                    )
                if len(tag) < 1:
                    raise ValueError("Tags cannot be empty strings")
        return v


class UpdateProjectSettingsRequest(BaseModel):
    """
    Request schema for updating project settings.

    Settings are replaced entirely (not merged).

    Attributes:
        settings: New settings to apply
    """

    model_config = ConfigDict(extra="forbid")

    settings: ProjectSettingsSchema = Field(
        description="New project settings",
    )


class MoveJobRequest(BaseModel):
    """
    Request schema for moving a job to a different project.

    Note: This schema is used with POST /projects/{project_id}/jobs/{job_id}/move
    The target project is in the URL path, not the body.
    """

    model_config = ConfigDict(extra="forbid")

    # Currently empty - target project is in URL path
    # Future: could add fields like `reason` for audit


# =============================================================================
# Response Schemas
# =============================================================================


class ProjectSummary(BaseModel):
    """
    Summary view of a project for list endpoints.

    Includes basic metadata and aggregated counts for quick overview.

    Attributes:
        id: Project UUID
        name: Project name
        slug: URL-safe identifier
        description: Project description (truncated if needed)
        status: Current status (active/archived)
        job_count: Number of jobs in project
        entity_count: Number of entities across all project jobs
        tags: Project tags
        created_at: When project was created
        updated_at: When project was last updated
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(
        description="Project UUID",
        json_schema_extra={"example": "550e8400-e29b-41d4-a716-446655440000"},
    )
    name: str = Field(
        description="Project name",
        json_schema_extra={"example": "Climate Research"},
    )
    slug: str = Field(
        description="URL-safe identifier",
        json_schema_extra={"example": "climate-research"},
    )
    description: Optional[str] = Field(
        description="Project description",
        json_schema_extra={"example": "Research on climate change data sources"},
    )
    status: str = Field(
        description="Current status: active or archived",
        json_schema_extra={"example": "active"},
    )
    job_count: int = Field(
        default=0,
        description="Number of jobs",
        ge=0,
        json_schema_extra={"example": 5},
    )
    entity_count: int = Field(
        default=0,
        description="Number of entities",
        ge=0,
        json_schema_extra={"example": 1250},
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Project tags",
        json_schema_extra={"example": ["research", "climate"]},
    )
    created_at: datetime = Field(
        description="Creation timestamp",
        json_schema_extra={"example": "2025-12-17T10:00:00Z"},
    )
    updated_at: datetime = Field(
        description="Last update timestamp",
        json_schema_extra={"example": "2025-12-17T15:30:00Z"},
    )


class ProjectDetail(BaseModel):
    """
    Detailed view of a project with full metadata and statistics.

    Used for single project retrieval endpoints.

    Attributes:
        id: Project UUID
        tenant_id: Owning tenant UUID
        created_by_user_id: User who created the project
        name: Project name
        slug: URL-safe identifier
        description: Full project description
        status: Current status (active/archived)
        settings: Project settings
        tags: Project tags
        archived_at: When archived (null if active)
        created_at: When project was created
        updated_at: When project was last updated
        job_count: Number of jobs
        page_count: Number of scraped pages
        entity_count: Number of extracted entities
        relationship_count: Number of entity relationships
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(
        description="Project UUID",
        json_schema_extra={"example": "550e8400-e29b-41d4-a716-446655440000"},
    )
    tenant_id: UUID = Field(
        description="Owning tenant UUID",
        json_schema_extra={"example": "660e8400-e29b-41d4-a716-446655440001"},
    )
    created_by_user_id: str = Field(
        description="Creator user ID",
        json_schema_extra={"example": "user-abc-123"},
    )
    name: str = Field(
        description="Project name",
        json_schema_extra={"example": "Climate Research"},
    )
    slug: str = Field(
        description="URL-safe identifier",
        json_schema_extra={"example": "climate-research"},
    )
    description: Optional[str] = Field(
        description="Project description",
        json_schema_extra={"example": "Research on climate change data sources"},
    )
    status: str = Field(
        description="Current status: active or archived",
        json_schema_extra={"example": "active"},
    )
    settings: dict = Field(
        default_factory=dict,
        description="Project settings",
        json_schema_extra={
            "example": {
                "default_extraction_strategy": "auto_detect",
                "enable_timeline_extraction": True,
            }
        },
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Project tags",
        json_schema_extra={"example": ["research", "climate"]},
    )
    archived_at: Optional[datetime] = Field(
        default=None,
        description="Archive timestamp",
        json_schema_extra={"example": None},
    )
    created_at: datetime = Field(
        description="Creation timestamp",
        json_schema_extra={"example": "2025-12-17T10:00:00Z"},
    )
    updated_at: datetime = Field(
        description="Last update timestamp",
        json_schema_extra={"example": "2025-12-17T15:30:00Z"},
    )

    # Statistics
    job_count: int = Field(
        default=0,
        description="Number of jobs",
        ge=0,
        json_schema_extra={"example": 5},
    )
    page_count: int = Field(
        default=0,
        description="Number of scraped pages",
        ge=0,
        json_schema_extra={"example": 250},
    )
    entity_count: int = Field(
        default=0,
        description="Number of entities",
        ge=0,
        json_schema_extra={"example": 1250},
    )
    relationship_count: int = Field(
        default=0,
        description="Number of relationships",
        ge=0,
        json_schema_extra={"example": 3500},
    )


class ProjectStatsResponse(BaseModel):
    """
    Detailed statistics for a project.

    Includes breakdowns by job status and entity type.

    Attributes:
        project_id: Project UUID
        job_count: Total number of jobs
        jobs_by_status: Breakdown by job status
        page_count: Total number of scraped pages
        entity_count: Total number of entities
        entities_by_type: Breakdown by entity type
        relationship_count: Total number of relationships
    """

    model_config = ConfigDict(from_attributes=True)

    project_id: UUID = Field(
        description="Project UUID",
        json_schema_extra={"example": "550e8400-e29b-41d4-a716-446655440000"},
    )
    job_count: int = Field(
        description="Total jobs",
        ge=0,
        json_schema_extra={"example": 5},
    )
    jobs_by_status: dict[str, int] = Field(
        default_factory=dict,
        description="Jobs grouped by status (pending, running, completed, failed)",
        json_schema_extra={
            "example": {"pending": 1, "running": 2, "completed": 2, "failed": 0}
        },
    )
    page_count: int = Field(
        description="Total scraped pages",
        ge=0,
        json_schema_extra={"example": 250},
    )
    entity_count: int = Field(
        description="Total entities",
        ge=0,
        json_schema_extra={"example": 1250},
    )
    entities_by_type: dict[str, int] = Field(
        default_factory=dict,
        description="Entities grouped by type",
        json_schema_extra={
            "example": {"person": 500, "organization": 300, "event": 450}
        },
    )
    relationship_count: int = Field(
        description="Total relationships",
        ge=0,
        json_schema_extra={"example": 3500},
    )


class PaginatedProjectResponse(BaseModel):
    """
    Paginated response for project list endpoints.

    Attributes:
        items: List of project summaries
        total: Total number of projects matching filters
        limit: Maximum items per page
        offset: Number of items skipped
        has_more: Whether more items exist beyond this page
    """

    items: list[ProjectSummary] = Field(description="Project summaries")
    total: int = Field(
        description="Total matching projects",
        ge=0,
        json_schema_extra={"example": 42},
    )
    limit: int = Field(
        description="Page size limit",
        ge=1,
        json_schema_extra={"example": 20},
    )
    offset: int = Field(
        description="Items skipped",
        ge=0,
        json_schema_extra={"example": 0},
    )
    has_more: bool = Field(
        description="More items available",
        json_schema_extra={"example": True},
    )


class MoveJobResponse(BaseModel):
    """
    Response for job movement operation.

    Attributes:
        status: Operation status ("moved")
        job_id: Job that was moved
        source_project_id: Project job was moved from
        target_project_id: Project job was moved to
    """

    status: str = Field(
        description="Operation status",
        json_schema_extra={"example": "moved"},
    )
    job_id: UUID = Field(
        description="Job UUID",
        json_schema_extra={"example": "770e8400-e29b-41d4-a716-446655440002"},
    )
    source_project_id: UUID = Field(
        description="Source project UUID",
        json_schema_extra={"example": "550e8400-e29b-41d4-a716-446655440000"},
    )
    target_project_id: UUID = Field(
        description="Target project UUID",
        json_schema_extra={"example": "880e8400-e29b-41d4-a716-446655440003"},
    )


class ArchiveRestoreResponse(BaseModel):
    """
    Response for archive/restore operations.

    Attributes:
        status: New project status ("archived" or "active")
        project_id: Project UUID
    """

    status: str = Field(
        description="New project status",
        json_schema_extra={"example": "archived"},
    )
    project_id: UUID = Field(
        description="Project UUID",
        json_schema_extra={"example": "550e8400-e29b-41d4-a716-446655440000"},
    )


class DeleteProjectResponse(BaseModel):
    """
    Response for project deletion.

    Attributes:
        status: Operation status ("deleted")
        project_id: Deleted project UUID
    """

    status: str = Field(
        description="Operation status",
        json_schema_extra={"example": "deleted"},
    )
    project_id: UUID = Field(
        description="Deleted project UUID",
        json_schema_extra={"example": "550e8400-e29b-41d4-a716-446655440000"},
    )


class ProjectEntitySummary(BaseModel):
    """
    Entity summary for project entity listing.

    Includes source information (job, page) for context within a project.

    Attributes:
        id: Entity UUID
        name: Entity name
        normalized_name: Normalized entity name for deduplication
        entity_type: Entity type (PERSON, ORGANIZATION, etc.)
        description: Entity description
        confidence_score: Extraction confidence (0.0-1.0)
        source_job_id: Job that extracted this entity
        source_job_name: Name of the source job
        source_page_id: Page entity was extracted from
        source_page_url: URL of the source page
        created_at: When entity was extracted
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(
        description="Entity UUID",
        json_schema_extra={"example": "990e8400-e29b-41d4-a716-446655440004"},
    )
    name: str = Field(
        description="Entity name",
        json_schema_extra={"example": "Albert Einstein"},
    )
    normalized_name: str = Field(
        description="Normalized entity name for deduplication",
        json_schema_extra={"example": "albert einstein"},
    )
    entity_type: str = Field(
        description="Entity type (person, organization, concept, etc.)",
        json_schema_extra={"example": "person"},
    )
    description: Optional[str] = Field(
        default=None,
        description="Entity description",
        json_schema_extra={"example": "German-born theoretical physicist"},
    )
    confidence_score: float = Field(
        description="Extraction confidence (0.0-1.0)",
        ge=0.0,
        le=1.0,
        json_schema_extra={"example": 0.95},
    )

    # Source information
    source_job_id: UUID = Field(
        description="Job that extracted this entity",
        json_schema_extra={"example": "770e8400-e29b-41d4-a716-446655440002"},
    )
    source_job_name: str = Field(
        description="Name of the source job",
        json_schema_extra={"example": "Wikipedia Physics Crawl"},
    )
    source_page_id: UUID = Field(
        description="Page entity was extracted from",
        json_schema_extra={"example": "880e8400-e29b-41d4-a716-446655440003"},
    )
    source_page_url: str = Field(
        description="URL of the source page",
        json_schema_extra={"example": "https://en.wikipedia.org/wiki/Albert_Einstein"},
    )

    created_at: datetime = Field(
        description="When entity was extracted",
        json_schema_extra={"example": "2025-12-17T10:00:00Z"},
    )


class PaginatedEntityResponse(BaseModel):
    """
    Paginated response for project entity list endpoints.

    Attributes:
        items: List of entity summaries
        total: Total number of entities matching filters
        limit: Maximum items per page
        offset: Number of items skipped
        has_more: Whether more items exist beyond this page
    """

    items: list[ProjectEntitySummary] = Field(description="Entity summaries")
    total: int = Field(
        description="Total matching entities",
        ge=0,
        json_schema_extra={"example": 1250},
    )
    limit: int = Field(
        description="Page size limit",
        ge=1,
        json_schema_extra={"example": 50},
    )
    offset: int = Field(
        description="Items skipped",
        ge=0,
        json_schema_extra={"example": 0},
    )
    has_more: bool = Field(
        description="More items available",
        json_schema_extra={"example": True},
    )


class ProjectJobSummary(BaseModel):
    """
    Summary view of a scraping job within a project context.

    Includes job status, progress, and timing information.

    Attributes:
        id: Job UUID
        name: Job name
        start_url: Starting URL for the crawl
        status: Current job status
        stage: Current pipeline stage
        pages_crawled: Number of pages scraped
        entities_extracted: Number of entities found
        extraction_strategy: Extraction strategy being used
        content_domain: Content domain ID (if using adaptive extraction)
        enable_timeline_extraction: Whether timeline extraction is enabled
        created_at: Job creation timestamp
        updated_at: Last update timestamp
        started_at: When job started running
        completed_at: When job completed
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(
        description="Job UUID",
        json_schema_extra={"example": "770e8400-e29b-41d4-a716-446655440002"},
    )
    name: str = Field(
        description="Job name",
        json_schema_extra={"example": "Climate Data Scraping"},
    )
    start_url: str = Field(
        description="Starting URL",
        json_schema_extra={"example": "https://climate.gov"},
    )
    status: str = Field(
        description="Current status: pending, queued, running, paused, completed, failed, cancelled",
        json_schema_extra={"example": "running"},
    )
    stage: Optional[str] = Field(
        default=None,
        description="Current pipeline stage: crawling, extracting, consolidating, done",
        json_schema_extra={"example": "extracting"},
    )
    pages_crawled: int = Field(
        default=0,
        description="Pages scraped so far",
        ge=0,
        json_schema_extra={"example": 42},
    )
    entities_extracted: int = Field(
        default=0,
        description="Entities found so far",
        ge=0,
        json_schema_extra={"example": 150},
    )
    extraction_strategy: str = Field(
        default="legacy",
        description="Extraction strategy: legacy, auto_detect, manual",
        json_schema_extra={"example": "auto_detect"},
    )
    content_domain: Optional[str] = Field(
        default=None,
        description="Content domain ID",
        json_schema_extra={"example": "technical_docs"},
    )
    enable_timeline_extraction: bool = Field(
        default=False,
        description="Whether timeline extraction is enabled",
        json_schema_extra={"example": True},
    )
    created_at: datetime = Field(
        description="Creation timestamp",
        json_schema_extra={"example": "2025-12-17T10:00:00Z"},
    )
    updated_at: datetime = Field(
        description="Last update timestamp",
        json_schema_extra={"example": "2025-12-17T15:30:00Z"},
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description="Start timestamp",
        json_schema_extra={"example": "2025-12-17T10:05:00Z"},
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="Completion timestamp",
        json_schema_extra={"example": None},
    )


class PaginatedJobsResponse(BaseModel):
    """
    Paginated response for project job list endpoints.

    Attributes:
        items: List of job summaries
        total: Total number of jobs matching filters
        limit: Maximum items per page
        offset: Number of items skipped
        has_more: Whether more items exist beyond this page
    """

    items: list[ProjectJobSummary] = Field(description="Job summaries")
    total: int = Field(
        description="Total matching jobs",
        ge=0,
        json_schema_extra={"example": 15},
    )
    limit: int = Field(
        description="Page size limit",
        ge=1,
        json_schema_extra={"example": 20},
    )
    offset: int = Field(
        description="Items skipped",
        ge=0,
        json_schema_extra={"example": 0},
    )
    has_more: bool = Field(
        description="More items available",
        json_schema_extra={"example": False},
    )


# =============================================================================
# Module Exports
# =============================================================================


__all__ = [
    # Settings
    "ProjectSettingsSchema",
    # Requests
    "CreateProjectRequest",
    "UpdateProjectRequest",
    "UpdateProjectSettingsRequest",
    "MoveJobRequest",
    # Responses
    "ProjectSummary",
    "ProjectDetail",
    "ProjectStatsResponse",
    "PaginatedProjectResponse",
    "ProjectEntitySummary",
    "PaginatedEntityResponse",
    "ProjectJobSummary",
    "PaginatedJobsResponse",
    "MoveJobResponse",
    "ArchiveRestoreResponse",
    "DeleteProjectResponse",
]
