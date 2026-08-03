"""
Unit tests for ScrapingJob model adaptive extraction properties.

Tests the uses_adaptive_extraction, needs_classification, and is_domain_resolved
property methods on the ScrapingJob model.
"""

import uuid

import pytest

from kg_builder.models.scraping_job import ScrapingJob


class TestScrapingJobAdaptiveProperties:
    """Tests for ScrapingJob adaptive extraction property methods."""

    @pytest.fixture
    def base_job_kwargs(self) -> dict:
        """Provide base kwargs for creating a ScrapingJob."""
        return {
            "tenant_id": uuid.uuid4(),
            "created_by_user_id": "test-user-123",
            "name": "Test Job",
            "start_url": "https://example.com",
        }

    def test_uses_adaptive_extraction_legacy(self, base_job_kwargs):
        """Test uses_adaptive_extraction is False for legacy strategy."""
        job = ScrapingJob(**base_job_kwargs, extraction_strategy="legacy")
        assert job.uses_adaptive_extraction is False

    def test_uses_adaptive_extraction_auto_detect(self, base_job_kwargs):
        """Test uses_adaptive_extraction is True for auto_detect strategy."""
        job = ScrapingJob(**base_job_kwargs, extraction_strategy="auto_detect")
        assert job.uses_adaptive_extraction is True

    def test_uses_adaptive_extraction_manual(self, base_job_kwargs):
        """Test uses_adaptive_extraction is True for manual strategy."""
        job = ScrapingJob(
            **base_job_kwargs,
            extraction_strategy="manual",
            content_domain="literature_fiction",
        )
        assert job.uses_adaptive_extraction is True

    def test_needs_classification_legacy(self, base_job_kwargs):
        """Test needs_classification is False for legacy strategy."""
        job = ScrapingJob(**base_job_kwargs, extraction_strategy="legacy")
        assert job.needs_classification is False

    def test_needs_classification_auto_detect_no_domain(self, base_job_kwargs):
        """Test needs_classification is True for auto_detect without domain."""
        job = ScrapingJob(**base_job_kwargs, extraction_strategy="auto_detect")
        assert job.needs_classification is True

    def test_needs_classification_auto_detect_with_domain(self, base_job_kwargs):
        """Test needs_classification is False for auto_detect with domain set."""
        job = ScrapingJob(
            **base_job_kwargs,
            extraction_strategy="auto_detect",
            content_domain="literature_fiction",
            classification_confidence=0.92,
        )
        assert job.needs_classification is False

    def test_needs_classification_manual(self, base_job_kwargs):
        """Test needs_classification is False for manual strategy."""
        job = ScrapingJob(
            **base_job_kwargs,
            extraction_strategy="manual",
            content_domain="literature_fiction",
        )
        assert job.needs_classification is False

    def test_is_domain_resolved_legacy(self, base_job_kwargs):
        """Test is_domain_resolved is True for legacy (doesn't use domains)."""
        job = ScrapingJob(**base_job_kwargs, extraction_strategy="legacy")
        assert job.is_domain_resolved is True

    def test_is_domain_resolved_auto_detect_no_domain(self, base_job_kwargs):
        """Test is_domain_resolved is False for auto_detect without domain."""
        job = ScrapingJob(**base_job_kwargs, extraction_strategy="auto_detect")
        assert job.is_domain_resolved is False

    def test_is_domain_resolved_auto_detect_with_domain(self, base_job_kwargs):
        """Test is_domain_resolved is True for auto_detect with domain."""
        job = ScrapingJob(
            **base_job_kwargs,
            extraction_strategy="auto_detect",
            content_domain="literature_fiction",
        )
        assert job.is_domain_resolved is True

    def test_is_domain_resolved_manual(self, base_job_kwargs):
        """Test is_domain_resolved is True for manual with domain."""
        job = ScrapingJob(
            **base_job_kwargs,
            extraction_strategy="manual",
            content_domain="technical_documentation",
        )
        assert job.is_domain_resolved is True

    def test_is_domain_resolved_manual_no_domain(self, base_job_kwargs):
        """Test is_domain_resolved is False for manual without domain.

        This shouldn't happen in practice due to validation, but the property
        should handle it correctly.
        """
        job = ScrapingJob(
            **base_job_kwargs,
            extraction_strategy="manual",
            # No content_domain - unusual but possible in model
        )
        assert job.is_domain_resolved is False


class TestScrapingJobAdaptiveDefaults:
    """Tests for ScrapingJob adaptive extraction default values."""

    @pytest.fixture
    def base_job_kwargs(self) -> dict:
        """Provide base kwargs for creating a ScrapingJob."""
        return {
            "tenant_id": uuid.uuid4(),
            "created_by_user_id": "test-user-123",
            "name": "Test Job",
            "start_url": "https://example.com",
        }

    def test_default_extraction_strategy(self, base_job_kwargs):
        """Test default extraction_strategy is 'legacy'."""
        job = ScrapingJob(**base_job_kwargs)
        assert job.extraction_strategy == "legacy"

    def test_default_content_domain(self, base_job_kwargs):
        """Test default content_domain is None."""
        job = ScrapingJob(**base_job_kwargs)
        assert job.content_domain is None

    def test_default_classification_confidence(self, base_job_kwargs):
        """Test default classification_confidence is None."""
        job = ScrapingJob(**base_job_kwargs)
        assert job.classification_confidence is None

    def test_default_inferred_schema(self, base_job_kwargs):
        """Test default inferred_schema is None."""
        job = ScrapingJob(**base_job_kwargs)
        assert job.inferred_schema is None

    def test_default_classification_sample_size(self, base_job_kwargs):
        """Test default classification_sample_size is 1."""
        job = ScrapingJob(**base_job_kwargs)
        assert job.classification_sample_size == 1


class TestScrapingJobAdaptiveExtractionFlow:
    """Tests simulating the full adaptive extraction lifecycle."""

    @pytest.fixture
    def base_job_kwargs(self) -> dict:
        """Provide base kwargs for creating a ScrapingJob."""
        return {
            "tenant_id": uuid.uuid4(),
            "created_by_user_id": "test-user-123",
            "name": "Test Job",
            "start_url": "https://example.com",
        }

    def test_auto_detect_classification_flow(self, base_job_kwargs):
        """Test the full auto_detect classification flow."""
        # Step 1: Job created with auto_detect
        job = ScrapingJob(
            **base_job_kwargs,
            extraction_strategy="auto_detect",
            classification_sample_size=3,
        )
        assert job.uses_adaptive_extraction is True
        assert job.needs_classification is True
        assert job.is_domain_resolved is False

        # Step 2: Classification completes
        job.content_domain = "literature_fiction"
        job.classification_confidence = 0.92
        job.inferred_schema = {
            "domain_id": "literature_fiction",
            "version": "1.0.0",
            "entity_types": ["character", "location", "theme"],
        }

        assert job.uses_adaptive_extraction is True
        assert job.needs_classification is False
        assert job.is_domain_resolved is True
        assert job.content_domain == "literature_fiction"
        assert job.classification_confidence == 0.92

    def test_manual_strategy_flow(self, base_job_kwargs):
        """Test manual strategy is immediately resolved."""
        job = ScrapingJob(
            **base_job_kwargs,
            extraction_strategy="manual",
            content_domain="technical_documentation",
            inferred_schema={
                "domain_id": "technical_documentation",
                "version": "1.0.0",
            },
        )
        assert job.uses_adaptive_extraction is True
        assert job.needs_classification is False  # Never needs classification
        assert job.is_domain_resolved is True

    def test_legacy_strategy_flow(self, base_job_kwargs):
        """Test legacy strategy never needs domain resolution."""
        job = ScrapingJob(**base_job_kwargs, extraction_strategy="legacy")
        assert job.uses_adaptive_extraction is False
        assert job.needs_classification is False
        assert job.is_domain_resolved is True
        assert job.content_domain is None  # Never set


class TestScrapingJobAdaptiveRepr:
    """Tests for ScrapingJob repr with adaptive fields."""

    @pytest.fixture
    def base_job_kwargs(self) -> dict:
        """Provide base kwargs for creating a ScrapingJob."""
        return {
            "tenant_id": uuid.uuid4(),
            "created_by_user_id": "test-user-123",
            "name": "Test Job",
            "start_url": "https://example.com",
        }

    def test_repr_includes_status_and_stage(self, base_job_kwargs):
        """Test repr includes status and stage."""
        job = ScrapingJob(**base_job_kwargs)
        repr_str = repr(job)
        assert "Test Job" in repr_str
        assert "pending" in repr_str
        assert "crawling" in repr_str
