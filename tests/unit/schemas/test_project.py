"""
Unit tests for project API schemas.

Tests the Pydantic models used in the project management API endpoints.
These schemas are defined in app/schemas/project.py as part of P4-001.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from kg_builder.schemas.project import (
    ArchiveRestoreResponse,
    # Requests
    CreateProjectRequest,
    DeleteProjectResponse,
    MoveJobRequest,
    MoveJobResponse,
    PaginatedProjectResponse,
    ProjectDetail,
    # Settings
    ProjectSettingsSchema,
    ProjectStatsResponse,
    # Responses
    ProjectSummary,
    UpdateProjectRequest,
    UpdateProjectSettingsRequest,
)

# =============================================================================
# Settings Schema Tests
# =============================================================================


class TestProjectSettingsSchema:
    """Tests for ProjectSettingsSchema."""

    def test_empty_settings_valid(self):
        """Test that empty settings are valid (all fields optional)."""
        settings = ProjectSettingsSchema()
        assert settings.default_extraction_provider_id is None
        assert settings.default_extraction_strategy is None
        assert settings.default_content_domain is None
        assert settings.enable_timeline_extraction is None

    def test_settings_with_all_fields(self):
        """Test settings with all fields populated."""
        provider_id = uuid4()
        settings = ProjectSettingsSchema(
            default_extraction_provider_id=provider_id,
            default_extraction_strategy="auto_detect",
            default_content_domain="literature_fiction",
            enable_timeline_extraction=True,
        )
        assert settings.default_extraction_provider_id == provider_id
        assert settings.default_extraction_strategy == "auto_detect"
        assert settings.default_content_domain == "literature_fiction"
        assert settings.enable_timeline_extraction is True

    def test_valid_extraction_strategies(self):
        """Test all valid extraction strategy values."""
        for strategy in ["legacy", "auto_detect", "manual"]:
            settings = ProjectSettingsSchema(default_extraction_strategy=strategy)
            assert settings.default_extraction_strategy == strategy

    def test_invalid_extraction_strategy(self):
        """Test that invalid extraction strategy raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ProjectSettingsSchema(default_extraction_strategy="invalid")
        assert "Invalid extraction strategy" in str(exc_info.value)

    def test_content_domain_max_length(self):
        """Test content domain max length validation."""
        # Valid: exactly 100 characters
        settings = ProjectSettingsSchema(default_content_domain="x" * 100)
        assert len(settings.default_content_domain) == 100

        # Invalid: exceeds 100 characters
        with pytest.raises(ValidationError):
            ProjectSettingsSchema(default_content_domain="x" * 101)

    def test_extra_fields_forbidden(self):
        """Test that extra fields are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ProjectSettingsSchema(unknown_field="value")
        assert "extra_forbidden" in str(exc_info.value)


# =============================================================================
# Create Project Request Tests
# =============================================================================


class TestCreateProjectRequest:
    """Tests for CreateProjectRequest."""

    def test_minimal_request_valid(self):
        """Test minimal valid create request (only name required)."""
        request = CreateProjectRequest(name="My Project")
        assert request.name == "My Project"
        assert request.description is None
        assert request.settings is None
        assert request.tags == []

    def test_full_request_valid(self):
        """Test create request with all fields."""
        settings = ProjectSettingsSchema(
            default_extraction_strategy="auto_detect",
            enable_timeline_extraction=True,
        )
        request = CreateProjectRequest(
            name="Climate Research",
            description="Research on climate change data",
            settings=settings,
            tags=["research", "climate", "priority"],
        )
        assert request.name == "Climate Research"
        assert request.description == "Research on climate change data"
        assert request.settings.default_extraction_strategy == "auto_detect"
        assert request.tags == ["research", "climate", "priority"]

    def test_name_required(self):
        """Test that name is required."""
        with pytest.raises(ValidationError) as exc_info:
            CreateProjectRequest(description="Missing name")
        errors = exc_info.value.errors()
        assert any(error["loc"] == ("name",) for error in errors)

    def test_name_too_short(self):
        """Test that empty name is rejected."""
        with pytest.raises(ValidationError):
            CreateProjectRequest(name="")

    def test_name_too_long(self):
        """Test that name exceeding 255 chars is rejected."""
        with pytest.raises(ValidationError):
            CreateProjectRequest(name="x" * 256)

    def test_name_exactly_255_chars(self):
        """Test that name of exactly 255 chars is accepted."""
        request = CreateProjectRequest(name="x" * 255)
        assert len(request.name) == 255

    def test_description_max_length(self):
        """Test description max length validation."""
        # Valid: exactly 2000 characters
        request = CreateProjectRequest(name="Test", description="x" * 2000)
        assert len(request.description) == 2000

        # Invalid: exceeds 2000 characters
        with pytest.raises(ValidationError):
            CreateProjectRequest(name="Test", description="x" * 2001)

    def test_tags_max_count(self):
        """Test tags max count validation."""
        # Valid: exactly 20 tags
        request = CreateProjectRequest(
            name="Test",
            tags=[f"tag{i}" for i in range(20)],
        )
        assert len(request.tags) == 20

        # Invalid: exceeds 20 tags
        with pytest.raises(ValidationError):
            CreateProjectRequest(
                name="Test",
                tags=[f"tag{i}" for i in range(21)],
            )

    def test_tag_too_long(self):
        """Test individual tag length validation."""
        with pytest.raises(ValidationError) as exc_info:
            CreateProjectRequest(name="Test", tags=["x" * 51])
        assert "exceeds maximum length" in str(exc_info.value)

    def test_empty_tag_rejected(self):
        """Test that empty tags are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CreateProjectRequest(name="Test", tags=["valid", ""])
        assert "empty strings" in str(exc_info.value)

    def test_extra_fields_forbidden(self):
        """Test that extra fields are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CreateProjectRequest(name="Test", unknown_field="value")
        assert "extra_forbidden" in str(exc_info.value)


# =============================================================================
# Update Project Request Tests
# =============================================================================


class TestUpdateProjectRequest:
    """Tests for UpdateProjectRequest."""

    def test_all_fields_optional(self):
        """Test that all fields are optional."""
        request = UpdateProjectRequest()
        assert request.name is None
        assert request.description is None
        assert request.tags is None

    def test_partial_update(self):
        """Test partial update with only name."""
        request = UpdateProjectRequest(name="New Name")
        assert request.name == "New Name"
        assert request.description is None
        assert request.tags is None

    def test_full_update(self):
        """Test update with all fields."""
        request = UpdateProjectRequest(
            name="Updated Name",
            description="Updated description",
            tags=["new", "tags"],
        )
        assert request.name == "Updated Name"
        assert request.description == "Updated description"
        assert request.tags == ["new", "tags"]

    def test_name_validation(self):
        """Test name validation rules apply on update."""
        # Empty name rejected
        with pytest.raises(ValidationError):
            UpdateProjectRequest(name="")

        # Too long name rejected
        with pytest.raises(ValidationError):
            UpdateProjectRequest(name="x" * 256)

    def test_tags_validation(self):
        """Test tags validation on update."""
        # Tag too long
        with pytest.raises(ValidationError) as exc_info:
            UpdateProjectRequest(tags=["x" * 51])
        assert "exceeds maximum length" in str(exc_info.value)

        # Empty tag
        with pytest.raises(ValidationError) as exc_info:
            UpdateProjectRequest(tags=["valid", ""])
        assert "empty strings" in str(exc_info.value)

    def test_extra_fields_forbidden(self):
        """Test that extra fields are rejected."""
        with pytest.raises(ValidationError):
            UpdateProjectRequest(unknown_field="value")


# =============================================================================
# Update Project Settings Request Tests
# =============================================================================


class TestUpdateProjectSettingsRequest:
    """Tests for UpdateProjectSettingsRequest."""

    def test_settings_required(self):
        """Test that settings field is required."""
        with pytest.raises(ValidationError):
            UpdateProjectSettingsRequest()

    def test_valid_settings_update(self):
        """Test valid settings update."""
        request = UpdateProjectSettingsRequest(
            settings=ProjectSettingsSchema(
                default_extraction_strategy="manual",
                default_content_domain="technical_docs",
            )
        )
        assert request.settings.default_extraction_strategy == "manual"
        assert request.settings.default_content_domain == "technical_docs"

    def test_empty_settings_valid(self):
        """Test that empty settings object is valid (clears settings)."""
        request = UpdateProjectSettingsRequest(settings=ProjectSettingsSchema())
        assert request.settings.default_extraction_strategy is None


# =============================================================================
# Move Job Request Tests
# =============================================================================


class TestMoveJobRequest:
    """Tests for MoveJobRequest."""

    def test_empty_request_valid(self):
        """Test that empty move job request is valid."""
        request = MoveJobRequest()
        # Currently empty schema, just validates structure

    def test_extra_fields_forbidden(self):
        """Test that extra fields are rejected."""
        with pytest.raises(ValidationError):
            MoveJobRequest(unknown_field="value")


# =============================================================================
# Project Summary Tests
# =============================================================================


class TestProjectSummary:
    """Tests for ProjectSummary response schema."""

    def test_minimal_summary(self):
        """Test minimal valid project summary."""
        project_id = uuid4()
        now = datetime.now(UTC)
        summary = ProjectSummary(
            id=project_id,
            name="Test Project",
            slug="test-project",
            description=None,
            status="active",
            created_at=now,
            updated_at=now,
        )
        assert summary.id == project_id
        assert summary.name == "Test Project"
        assert summary.slug == "test-project"
        assert summary.status == "active"
        assert summary.job_count == 0
        assert summary.entity_count == 0
        assert summary.tags == []

    def test_full_summary(self):
        """Test project summary with all fields."""
        project_id = uuid4()
        now = datetime.now(UTC)
        summary = ProjectSummary(
            id=project_id,
            name="Climate Research",
            slug="climate-research",
            description="Research on climate data",
            status="active",
            job_count=5,
            entity_count=1250,
            tags=["research", "climate"],
            created_at=now,
            updated_at=now,
        )
        assert summary.job_count == 5
        assert summary.entity_count == 1250
        assert summary.tags == ["research", "climate"]

    def test_archived_status(self):
        """Test summary with archived status."""
        summary = ProjectSummary(
            id=uuid4(),
            name="Archived Project",
            slug="archived-project",
            description=None,
            status="archived",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        assert summary.status == "archived"


# =============================================================================
# Project Detail Tests
# =============================================================================


class TestProjectDetail:
    """Tests for ProjectDetail response schema."""

    def test_minimal_detail(self):
        """Test minimal valid project detail."""
        project_id = uuid4()
        tenant_id = uuid4()
        now = datetime.now(UTC)
        detail = ProjectDetail(
            id=project_id,
            tenant_id=tenant_id,
            created_by_user_id="user-123",
            name="Test Project",
            slug="test-project",
            description=None,
            status="active",
            created_at=now,
            updated_at=now,
        )
        assert detail.id == project_id
        assert detail.tenant_id == tenant_id
        assert detail.created_by_user_id == "user-123"
        assert detail.job_count == 0
        assert detail.page_count == 0
        assert detail.entity_count == 0
        assert detail.relationship_count == 0
        assert detail.settings == {}
        assert detail.tags == []
        assert detail.archived_at is None

    def test_full_detail(self):
        """Test project detail with all fields."""
        project_id = uuid4()
        tenant_id = uuid4()
        now = datetime.now(UTC)
        archived_time = datetime(2025, 12, 15, 10, 0, 0, tzinfo=UTC)
        detail = ProjectDetail(
            id=project_id,
            tenant_id=tenant_id,
            created_by_user_id="user-456",
            name="Full Project",
            slug="full-project",
            description="A full project with all data",
            status="archived",
            settings={
                "default_extraction_strategy": "auto_detect",
                "enable_timeline_extraction": True,
            },
            tags=["complete", "test"],
            archived_at=archived_time,
            created_at=now,
            updated_at=now,
            job_count=10,
            page_count=500,
            entity_count=2500,
            relationship_count=7500,
        )
        assert detail.status == "archived"
        assert detail.archived_at == archived_time
        assert detail.job_count == 10
        assert detail.page_count == 500
        assert detail.entity_count == 2500
        assert detail.relationship_count == 7500
        assert detail.settings["default_extraction_strategy"] == "auto_detect"


# =============================================================================
# Project Stats Response Tests
# =============================================================================


class TestProjectStatsResponse:
    """Tests for ProjectStatsResponse schema."""

    def test_minimal_stats(self):
        """Test minimal valid stats response."""
        project_id = uuid4()
        stats = ProjectStatsResponse(
            project_id=project_id,
            job_count=0,
            page_count=0,
            entity_count=0,
            relationship_count=0,
        )
        assert stats.project_id == project_id
        assert stats.jobs_by_status == {}
        assert stats.entities_by_type == {}

    def test_full_stats(self):
        """Test stats response with all data."""
        project_id = uuid4()
        stats = ProjectStatsResponse(
            project_id=project_id,
            job_count=5,
            jobs_by_status={
                "pending": 1,
                "running": 2,
                "completed": 2,
                "failed": 0,
            },
            page_count=250,
            entity_count=1250,
            entities_by_type={
                "person": 500,
                "organization": 300,
                "event": 450,
            },
            relationship_count=3500,
        )
        assert stats.job_count == 5
        assert stats.jobs_by_status["completed"] == 2
        assert stats.entities_by_type["person"] == 500


# =============================================================================
# Paginated Project Response Tests
# =============================================================================


class TestPaginatedProjectResponse:
    """Tests for PaginatedProjectResponse schema."""

    def test_empty_page(self):
        """Test paginated response with no items."""
        response = PaginatedProjectResponse(
            items=[],
            total=0,
            limit=20,
            offset=0,
            has_more=False,
        )
        assert response.items == []
        assert response.total == 0
        assert response.has_more is False

    def test_page_with_items(self):
        """Test paginated response with items."""
        now = datetime.now(UTC)
        items = [
            ProjectSummary(
                id=uuid4(),
                name=f"Project {i}",
                slug=f"project-{i}",
                description=None,
                status="active",
                created_at=now,
                updated_at=now,
            )
            for i in range(5)
        ]
        response = PaginatedProjectResponse(
            items=items,
            total=42,
            limit=5,
            offset=0,
            has_more=True,
        )
        assert len(response.items) == 5
        assert response.total == 42
        assert response.has_more is True

    def test_middle_page(self):
        """Test paginated response for middle page."""
        response = PaginatedProjectResponse(
            items=[],
            total=100,
            limit=20,
            offset=40,
            has_more=True,
        )
        assert response.offset == 40
        assert response.has_more is True


# =============================================================================
# Move Job Response Tests
# =============================================================================


class TestMoveJobResponse:
    """Tests for MoveJobResponse schema."""

    def test_move_response(self):
        """Test move job response."""
        job_id = uuid4()
        source_id = uuid4()
        target_id = uuid4()
        response = MoveJobResponse(
            status="moved",
            job_id=job_id,
            source_project_id=source_id,
            target_project_id=target_id,
        )
        assert response.status == "moved"
        assert response.job_id == job_id
        assert response.source_project_id == source_id
        assert response.target_project_id == target_id


# =============================================================================
# Archive/Restore Response Tests
# =============================================================================


class TestArchiveRestoreResponse:
    """Tests for ArchiveRestoreResponse schema."""

    def test_archive_response(self):
        """Test archive response."""
        project_id = uuid4()
        response = ArchiveRestoreResponse(
            status="archived",
            project_id=project_id,
        )
        assert response.status == "archived"
        assert response.project_id == project_id

    def test_restore_response(self):
        """Test restore response."""
        project_id = uuid4()
        response = ArchiveRestoreResponse(
            status="active",
            project_id=project_id,
        )
        assert response.status == "active"
        assert response.project_id == project_id


# =============================================================================
# Delete Project Response Tests
# =============================================================================


class TestDeleteProjectResponse:
    """Tests for DeleteProjectResponse schema."""

    def test_delete_response(self):
        """Test delete project response."""
        project_id = uuid4()
        response = DeleteProjectResponse(
            status="deleted",
            project_id=project_id,
        )
        assert response.status == "deleted"
        assert response.project_id == project_id


# =============================================================================
# Serialization Tests
# =============================================================================


class TestSerialization:
    """Tests for JSON serialization of schemas."""

    def test_project_summary_to_dict(self):
        """Test ProjectSummary serialization."""
        project_id = uuid4()
        now = datetime.now(UTC)
        summary = ProjectSummary(
            id=project_id,
            name="Test",
            slug="test",
            description="Test description",
            status="active",
            job_count=5,
            entity_count=100,
            tags=["tag1", "tag2"],
            created_at=now,
            updated_at=now,
        )
        data = summary.model_dump(mode="json")
        assert data["id"] == str(project_id)
        assert data["name"] == "Test"
        assert data["job_count"] == 5
        assert data["tags"] == ["tag1", "tag2"]
        assert isinstance(data["created_at"], str)

    def test_project_settings_to_dict(self):
        """Test ProjectSettingsSchema serialization."""
        provider_id = uuid4()
        settings = ProjectSettingsSchema(
            default_extraction_provider_id=provider_id,
            default_extraction_strategy="manual",
            enable_timeline_extraction=True,
        )
        data = settings.model_dump(mode="json")
        assert data["default_extraction_provider_id"] == str(provider_id)
        assert data["default_extraction_strategy"] == "manual"
        assert data["enable_timeline_extraction"] is True

    def test_paginated_response_to_dict(self):
        """Test PaginatedProjectResponse serialization."""
        now = datetime.now(UTC)
        response = PaginatedProjectResponse(
            items=[
                ProjectSummary(
                    id=uuid4(),
                    name="Test",
                    slug="test",
                    description=None,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            ],
            total=1,
            limit=20,
            offset=0,
            has_more=False,
        )
        data = response.model_dump(mode="json")
        assert len(data["items"]) == 1
        assert data["total"] == 1
        assert isinstance(data["items"][0]["id"], str)
