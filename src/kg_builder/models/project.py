"""
Project model for workspace management.

This module defines the Project model, which represents a logical workspace
for organizing scraping jobs. Projects enable users to group related jobs
by research topic, view consolidated entities, and manage project-level settings.

See FRD: docs/tasks/project-workspace-management/frd-project-workspace-management.md
See ADR: docs/decisions/ADR-XXX (pending) for event sourcing decisions
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kg_builder.db import Base

if TYPE_CHECKING:
    from kg_builder.models.scraping_job import ScrapingJob
    from kg_builder.models.tenant import Tenant


class ProjectStatus(str, enum.Enum):
    """Enumeration of project lifecycle states."""

    ACTIVE = "active"  # Normal working state
    ARCHIVED = "archived"  # Soft-deleted, read-only state


class Project(Base):
    """
    Represents a workspace for organizing scraping jobs.

    Projects enable users to:
    - Group related scraping jobs by research topic
    - View consolidated entities across all project jobs
    - Configure project-level default settings for extraction
    - Archive/restore projects for lifecycle management

    Each project belongs to a tenant and contains zero or more scraping jobs.
    Projects have RLS enforcement for multi-tenant isolation.

    Attributes:
        id: UUID primary key for security
        tenant_id: Foreign key to tenant (RLS enforced)
        created_by_user_id: User who created the project
        name: Human-readable project name
        slug: URL-safe identifier, unique per tenant
        description: Optional project description
        status: Project lifecycle status (active or archived)
        archived_at: Timestamp when project was archived
        settings: JSONB settings for extraction defaults, LLM preferences
        tags: Array of categorization tags
        created_at: Timestamp of creation (inherited from Base)
        updated_at: Timestamp of last update (inherited from Base)

    Relationships:
        tenant: Parent tenant this project belongs to
        jobs: Scraping jobs within this project
    """

    __tablename__ = "projects"

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
        comment="Owning tenant (RLS enforced)",
    )

    # Project ownership
    created_by_user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="User ID from JWT sub claim",
    )

    # Project identification
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Human-readable project name",
    )

    slug: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="URL-safe identifier, unique per tenant",
    )

    # Optional description
    description: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
        comment="Optional project description",
    )

    # Project lifecycle
    status: Mapped[ProjectStatus] = mapped_column(
        SQLEnum(
            ProjectStatus,
            name="project_status",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,  # Type created in migration
        ),
        nullable=False,
        default=ProjectStatus.ACTIVE,
        insert_default=ProjectStatus.ACTIVE,
        index=True,
        comment="Project lifecycle status: active or archived",
    )

    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When project was archived (null if active)",
    )

    # Flexible configuration
    settings: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        insert_default=dict,
        comment="JSONB settings (extraction defaults, LLM preferences)",
    )

    # Categorization
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)),
        nullable=False,
        default=list,
        insert_default=list,
        comment="Array of categorization tags",
    )

    # Relationships
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        doc="Tenant this project belongs to",
    )

    jobs: Mapped[list[ScrapingJob]] = relationship(
        "ScrapingJob",
        back_populates="project",
        doc="Scraping jobs within this project",
    )

    def __init__(self, **kwargs):
        """Initialize project with default values for optional fields."""
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        if "status" not in kwargs:
            kwargs["status"] = ProjectStatus.ACTIVE
        if "settings" not in kwargs:
            kwargs["settings"] = {}
        if "tags" not in kwargs:
            kwargs["tags"] = []
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        """Return string representation of the project."""
        return f"<Project {self.id} '{self.name}' ({self.status.value})>"

    @property
    def is_active(self) -> bool:
        """Check if project is in active state."""
        return self.status == ProjectStatus.ACTIVE

    @property
    def is_archived(self) -> bool:
        """Check if project is archived."""
        return self.status == ProjectStatus.ARCHIVED
