"""
Unit tests for ScrapingJob timeline extraction feature.

Tests the enable_timeline_extraction column and default behavior
added in ADR-025 for the Timeline and Chronology Extraction feature.
"""

import uuid

import pytest

from kg_builder.models.scraping_job import JobStage, JobStatus, ScrapingJob


class TestScrapingJobTimelineExtraction:
    """Tests for ScrapingJob enable_timeline_extraction column."""

    @pytest.fixture
    def base_job_kwargs(self) -> dict:
        """Provide base kwargs for creating a ScrapingJob."""
        return {
            "tenant_id": uuid.uuid4(),
            "created_by_user_id": "test-user-123",
            "name": "Test Job",
            "start_url": "https://example.com",
        }

    def test_enable_timeline_extraction_defaults_to_false(self, base_job_kwargs):
        """Test enable_timeline_extraction defaults to False."""
        job = ScrapingJob(**base_job_kwargs)
        assert job.enable_timeline_extraction is False

    def test_enable_timeline_extraction_can_be_enabled(self, base_job_kwargs):
        """Test enable_timeline_extraction can be set to True."""
        job = ScrapingJob(
            **base_job_kwargs,
            enable_timeline_extraction=True,
        )
        assert job.enable_timeline_extraction is True

    def test_enable_timeline_extraction_explicit_false(self, base_job_kwargs):
        """Test enable_timeline_extraction can be explicitly set to False."""
        job = ScrapingJob(
            **base_job_kwargs,
            enable_timeline_extraction=False,
        )
        assert job.enable_timeline_extraction is False


class TestScrapingJobTimelineWithAdaptiveExtraction:
    """Tests for timeline extraction combined with adaptive extraction."""

    @pytest.fixture
    def base_job_kwargs(self) -> dict:
        """Provide base kwargs for creating a ScrapingJob."""
        return {
            "tenant_id": uuid.uuid4(),
            "created_by_user_id": "test-user-123",
            "name": "Test Job",
            "start_url": "https://example.com",
        }

    def test_timeline_with_legacy_strategy(self, base_job_kwargs):
        """Test timeline extraction works with legacy strategy."""
        job = ScrapingJob(
            **base_job_kwargs,
            extraction_strategy="legacy",
            enable_timeline_extraction=True,
        )
        assert job.extraction_strategy == "legacy"
        assert job.enable_timeline_extraction is True
        assert job.uses_adaptive_extraction is False

    def test_timeline_with_auto_detect_strategy(self, base_job_kwargs):
        """Test timeline extraction works with auto_detect strategy."""
        job = ScrapingJob(
            **base_job_kwargs,
            extraction_strategy="auto_detect",
            enable_timeline_extraction=True,
        )
        assert job.extraction_strategy == "auto_detect"
        assert job.enable_timeline_extraction is True
        assert job.uses_adaptive_extraction is True

    def test_timeline_with_manual_strategy(self, base_job_kwargs):
        """Test timeline extraction works with manual strategy."""
        job = ScrapingJob(
            **base_job_kwargs,
            extraction_strategy="manual",
            content_domain="literature_fiction",
            enable_timeline_extraction=True,
        )
        assert job.extraction_strategy == "manual"
        assert job.enable_timeline_extraction is True
        assert job.uses_adaptive_extraction is True


class TestScrapingJobTimelineLifecycle:
    """Tests simulating timeline extraction lifecycle."""

    @pytest.fixture
    def base_job_kwargs(self) -> dict:
        """Provide base kwargs for creating a ScrapingJob."""
        return {
            "tenant_id": uuid.uuid4(),
            "created_by_user_id": "test-user-123",
            "name": "Historical Events Crawler",
            "start_url": "https://example.com/history",
        }

    def test_job_created_with_timeline_enabled(self, base_job_kwargs):
        """Test job creation with timeline extraction enabled."""
        job = ScrapingJob(
            **base_job_kwargs,
            extraction_strategy="auto_detect",
            enable_timeline_extraction=True,
        )

        # Initial state
        assert job.status == JobStatus.PENDING
        assert job.stage == JobStage.CRAWLING
        assert job.enable_timeline_extraction is True

    def test_timeline_flag_persists_through_status_changes(self, base_job_kwargs):
        """Test timeline flag remains set through job lifecycle."""
        job = ScrapingJob(
            **base_job_kwargs,
            enable_timeline_extraction=True,
        )

        # Simulate job lifecycle
        job.status = JobStatus.QUEUED
        assert job.enable_timeline_extraction is True

        job.status = JobStatus.RUNNING
        assert job.enable_timeline_extraction is True

        job.stage = JobStage.EXTRACTING
        assert job.enable_timeline_extraction is True

        job.stage = JobStage.CONSOLIDATING
        assert job.enable_timeline_extraction is True

        job.status = JobStatus.COMPLETED
        job.stage = JobStage.DONE
        assert job.enable_timeline_extraction is True
