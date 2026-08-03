"""
Domain events for project lifecycle management.

These events track the complete lifecycle of a project, from creation
through archival. They are designed to work with the ProjectAggregate
and enable event-driven updates to downstream systems.

Event types:
- ProjectCreated: New project initialized
- ProjectUpdated: Project metadata changed (name, description, tags)
- ProjectSettingsUpdated: Project settings changed (extraction defaults)
- ProjectArchived: Project moved to archived status
- ProjectRestored: Archived project restored to active
- JobMovedBetweenProjects: Job moved from one project to another

Note on aggregate_id vs project_id:
The project_id field in events serves the same role as aggregate_id
inherited from the base DomainEvent class. This is intentional for
domain clarity - when creating events via self.create_event(), the
aggregate_id is automatically set from the aggregate's ID. The explicit
project_id field provides clearer semantics in the event payload.
"""

from datetime import datetime
from typing import Annotated, Optional, Union
from uuid import UUID

from eventsource import register_event
from pydantic import BeforeValidator, Field

from kg_builder.events.base import TenantDomainEvent


def coerce_to_str(v: Union[str, UUID]) -> str:
    """Coerce UUID-like values to strings."""
    if isinstance(v, UUID):
        return str(v)
    return v


# Type that accepts both str and UUID but coerces to str
CoercedStr = Annotated[str, BeforeValidator(coerce_to_str)]


# =============================================================================
# Project Lifecycle Events
# =============================================================================


@register_event
class ProjectCreated(TenantDomainEvent):
    """
    Emitted when a new project is created.

    This event initializes a project with its core identity and metadata.
    The project starts in 'active' status and can begin accepting jobs
    immediately after creation.

    Attributes:
        project_id: Unique identifier for the project (same as aggregate_id)
        name: Human-readable project name (required, max 255 chars)
        slug: URL-safe identifier, unique within tenant
        description: Optional longer description of the project
        created_by_user_id: User ID from JWT sub claim who created the project
        settings: Initial project settings (extraction defaults, etc.)
        tags: Optional tags for categorization
        created_at: Timestamp when project was created
    """

    event_type: str = "ProjectCreated"
    aggregate_type: str = "Project"

    project_id: UUID = Field(description="Unique identifier for the project")
    name: str = Field(description="Human-readable project name")
    slug: str = Field(description="URL-safe identifier for project")
    description: Optional[str] = Field(default=None, description="Project description")
    created_by_user_id: CoercedStr = Field(description="User who created the project")
    settings: dict = Field(default_factory=dict, description="Initial project settings")
    tags: list[str] = Field(default_factory=list, description="Project tags")
    created_at: datetime = Field(description="Creation timestamp")


@register_event
class ProjectUpdated(TenantDomainEvent):
    """
    Emitted when project metadata is updated.

    This event captures changes to project metadata fields like name,
    description, and tags. Settings changes use ProjectSettingsUpdated.

    The event captures both old and new values for audit purposes and
    to enable conflict resolution if needed.

    Attributes:
        project_id: Project being updated
        updated_fields: List of field names that changed
        old_values: Previous values for changed fields
        new_values: New values for changed fields
        updated_by_user_id: User who made the update
        updated_at: Timestamp of the update
    """

    event_type: str = "ProjectUpdated"
    aggregate_type: str = "Project"

    project_id: UUID = Field(description="Project being updated")
    updated_fields: list[str] = Field(description="Fields that were changed")
    old_values: dict = Field(default_factory=dict, description="Previous values")
    new_values: dict = Field(default_factory=dict, description="New values")
    updated_by_user_id: CoercedStr = Field(description="User who made the update")
    updated_at: datetime = Field(description="Update timestamp")


@register_event
class ProjectSettingsUpdated(TenantDomainEvent):
    """
    Emitted when project settings are changed.

    Settings include extraction configuration defaults that are inherited
    by new jobs created within the project:
    - default_extraction_provider_id: UUID of default extraction provider
    - default_extraction_strategy: 'legacy', 'auto_detect', or 'manual'
    - default_content_domain: Content domain for manual strategy
    - enable_timeline_extraction: Whether to extract timeline data

    Attributes:
        project_id: Project being updated
        old_settings: Previous settings dictionary
        new_settings: New settings dictionary
        updated_by_user_id: User who changed settings
        updated_at: Timestamp of the update
    """

    event_type: str = "ProjectSettingsUpdated"
    aggregate_type: str = "Project"

    project_id: UUID = Field(description="Project being updated")
    old_settings: dict = Field(default_factory=dict, description="Previous settings")
    new_settings: dict = Field(default_factory=dict, description="New settings")
    updated_by_user_id: CoercedStr = Field(description="User who changed settings")
    updated_at: datetime = Field(description="Update timestamp")


@register_event
class ProjectArchived(TenantDomainEvent):
    """
    Emitted when a project is archived.

    Archiving is a soft delete that hides the project from the default
    list view but preserves all data. Archived projects:
    - Cannot have new jobs created
    - Cannot have jobs moved to them
    - Cannot have their settings updated
    - Can still be queried directly by ID
    - Can be restored to active status

    Attributes:
        project_id: Project being archived
        archived_by_user_id: User who archived the project
        archived_at: Timestamp when archived
        job_count: Number of jobs in project at archive time (for audit)
    """

    event_type: str = "ProjectArchived"
    aggregate_type: str = "Project"

    project_id: UUID = Field(description="Project being archived")
    archived_by_user_id: CoercedStr = Field(description="User who archived the project")
    archived_at: datetime = Field(description="Archive timestamp")
    job_count: int = Field(default=0, description="Jobs in project at archive time")


@register_event
class ProjectRestored(TenantDomainEvent):
    """
    Emitted when an archived project is restored.

    Restoration returns the project to active status, allowing:
    - New job creation
    - Job movement to this project
    - Settings updates

    Attributes:
        project_id: Project being restored
        restored_by_user_id: User who restored the project
        restored_at: Timestamp when restored
    """

    event_type: str = "ProjectRestored"
    aggregate_type: str = "Project"

    project_id: UUID = Field(description="Project being restored")
    restored_by_user_id: CoercedStr = Field(description="User who restored the project")
    restored_at: datetime = Field(description="Restore timestamp")


# =============================================================================
# Job-Project Relationship Events
# =============================================================================


@register_event
class JobMovedBetweenProjects(TenantDomainEvent):
    """
    Emitted when a job is moved from one project to another.

    Since all jobs MUST belong to a project (greenfield design), this event
    handles the movement of jobs between projects. Jobs cannot be "unassigned"
    so source_project_id is always required.

    This event is emitted on the SOURCE project's aggregate. The projection
    handler updates the job's project_id in the scraping_jobs table.

    Invariants:
    - source_project_id != target_project_id (enforced at API level)
    - Both projects must belong to same tenant (enforced at API level)
    - Target project must be active, not archived (enforced at API level)

    Attributes:
        job_id: Job being moved
        job_name: Name of the job (for audit readability)
        source_project_id: Project job is moving from
        target_project_id: Project job is moving to
        moved_by_user_id: User who moved the job
        moved_at: Timestamp of the move
    """

    event_type: str = "JobMovedBetweenProjects"
    aggregate_type: str = "Project"

    job_id: UUID = Field(description="Job being moved")
    job_name: str = Field(description="Name of the job being moved")
    source_project_id: UUID = Field(description="Project job is moving from")
    target_project_id: UUID = Field(description="Project job is moving to")
    moved_by_user_id: CoercedStr = Field(description="User who moved the job")
    moved_at: datetime = Field(description="Move timestamp")


# =============================================================================
# Module Exports
# =============================================================================


__all__ = [
    "ProjectCreated",
    "ProjectUpdated",
    "ProjectSettingsUpdated",
    "ProjectArchived",
    "ProjectRestored",
    "JobMovedBetweenProjects",
]
