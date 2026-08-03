"""
Unit tests for adaptive extraction fields in ScrapingJob schemas.

Tests the validation and behavior of extraction_strategy, content_domain,
and classification_sample_size fields in CreateScrapingJobRequest and
related response schemas.
"""

import pytest
from pydantic import ValidationError

from kg_builder.schemas.scraping import (
    CreateScrapingJobRequest,
    ScrapingJobResponse,
    ScrapingJobSummary,
)


class TestCreateScrapingJobRequestAdaptiveFields:
    """Tests for CreateScrapingJobRequest adaptive extraction fields."""

    def test_default_strategy_is_legacy(self):
        """Test that default extraction strategy is legacy."""
        job = CreateScrapingJobRequest(
            name="Test Job",
            start_url="https://example.com",
        )
        assert job.extraction_strategy == "legacy"
        assert job.content_domain is None
        assert job.classification_sample_size == 1

    def test_legacy_strategy_explicit(self):
        """Test explicit legacy strategy."""
        job = CreateScrapingJobRequest(
            name="Test Job",
            start_url="https://example.com",
            extraction_strategy="legacy",
        )
        assert job.extraction_strategy == "legacy"
        assert job.content_domain is None

    def test_auto_detect_strategy_without_domain(self):
        """Test auto_detect strategy without domain (valid)."""
        job = CreateScrapingJobRequest(
            name="Test Job",
            start_url="https://example.com",
            extraction_strategy="auto_detect",
        )
        assert job.extraction_strategy == "auto_detect"
        assert job.content_domain is None

    def test_auto_detect_strategy_with_sample_size(self):
        """Test auto_detect strategy with custom sample size."""
        job = CreateScrapingJobRequest(
            name="Test Job",
            start_url="https://example.com",
            extraction_strategy="auto_detect",
            classification_sample_size=3,
        )
        assert job.extraction_strategy == "auto_detect"
        assert job.classification_sample_size == 3

    def test_manual_strategy_requires_domain(self):
        """Test that manual strategy requires content_domain."""
        with pytest.raises(ValidationError) as exc_info:
            CreateScrapingJobRequest(
                name="Test Job",
                start_url="https://example.com",
                extraction_strategy="manual",
                # Missing content_domain
            )
        assert "content_domain is required" in str(exc_info.value)

    def test_manual_strategy_with_domain(self):
        """Test manual strategy with domain (valid)."""
        job = CreateScrapingJobRequest(
            name="Test Job",
            start_url="https://example.com",
            extraction_strategy="manual",
            content_domain="literature_fiction",
        )
        assert job.extraction_strategy == "manual"
        assert job.content_domain == "literature_fiction"

    def test_legacy_strategy_rejects_domain(self):
        """Test that legacy strategy rejects content_domain."""
        with pytest.raises(ValidationError) as exc_info:
            CreateScrapingJobRequest(
                name="Test Job",
                start_url="https://example.com",
                extraction_strategy="legacy",
                content_domain="literature_fiction",
            )
        assert "should not be set" in str(exc_info.value)

    def test_invalid_strategy_rejected(self):
        """Test that invalid strategy values are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CreateScrapingJobRequest(
                name="Test Job",
                start_url="https://example.com",
                extraction_strategy="invalid_strategy",
            )
        # Pydantic raises error for invalid literal
        assert "extraction_strategy" in str(exc_info.value).lower()

    def test_classification_sample_size_minimum(self):
        """Test sample size minimum bound (1)."""
        with pytest.raises(ValidationError) as exc_info:
            CreateScrapingJobRequest(
                name="Test Job",
                start_url="https://example.com",
                extraction_strategy="auto_detect",
                classification_sample_size=0,
            )
        assert "classification_sample_size" in str(exc_info.value).lower()

    def test_classification_sample_size_maximum(self):
        """Test sample size maximum bound (5)."""
        with pytest.raises(ValidationError) as exc_info:
            CreateScrapingJobRequest(
                name="Test Job",
                start_url="https://example.com",
                extraction_strategy="auto_detect",
                classification_sample_size=10,
            )
        assert "classification_sample_size" in str(exc_info.value).lower()

    def test_classification_sample_size_valid_range(self):
        """Test valid sample sizes in range 1-5."""
        for size in [1, 2, 3, 4, 5]:
            job = CreateScrapingJobRequest(
                name="Test Job",
                start_url="https://example.com",
                extraction_strategy="auto_detect",
                classification_sample_size=size,
            )
            assert job.classification_sample_size == size

    def test_content_domain_max_length(self):
        """Test content_domain max length constraint (50 chars)."""
        # Valid: exactly 50 chars
        job = CreateScrapingJobRequest(
            name="Test Job",
            start_url="https://example.com",
            extraction_strategy="manual",
            content_domain="a" * 50,
        )
        assert len(job.content_domain) == 50

        # Invalid: 51 chars
        with pytest.raises(ValidationError):
            CreateScrapingJobRequest(
                name="Test Job",
                start_url="https://example.com",
                extraction_strategy="manual",
                content_domain="a" * 51,
            )

    def test_auto_detect_allows_domain_for_pre_selection(self):
        """Test auto_detect can accept optional domain hint."""
        # This is valid - user can pre-select domain even with auto_detect
        # (domain would be used if classification agrees or as fallback)
        job = CreateScrapingJobRequest(
            name="Test Job",
            start_url="https://example.com",
            extraction_strategy="auto_detect",
            content_domain="literature_fiction",
        )
        assert job.extraction_strategy == "auto_detect"
        assert job.content_domain == "literature_fiction"


class TestScrapingJobResponseAdaptiveFields:
    """Tests for ScrapingJobResponse adaptive extraction fields."""

    def test_response_with_legacy_strategy(self):
        """Test response schema with legacy strategy defaults."""
        # This simulates the from_attributes behavior
        response_data = {
            "id": "12345678-1234-1234-1234-123456789012",
            "tenant_id": "12345678-1234-1234-1234-123456789012",
            "created_by_user_id": "user123",
            "name": "Test Job",
            "start_url": "https://example.com",
            "allowed_domains": ["example.com"],
            "crawl_depth": 2,
            "max_pages": 100,
            "crawl_speed": 1.0,
            "respect_robots_txt": True,
            "use_llm_extraction": True,
            "custom_settings": {},
            "status": "pending",
            "pages_crawled": 0,
            "entities_extracted": 0,
            "errors_count": 0,
            "created_at": "2025-12-15T00:00:00Z",
            "updated_at": "2025-12-15T00:00:00Z",
            # Adaptive fields with defaults
            "extraction_strategy": "legacy",
            "content_domain": None,
            "classification_confidence": None,
            "classification_sample_size": 1,
            "uses_adaptive_extraction": False,
            "is_domain_resolved": True,
        }
        response = ScrapingJobResponse(**response_data)
        assert response.extraction_strategy == "legacy"
        assert response.content_domain is None
        assert response.uses_adaptive_extraction is False
        assert response.is_domain_resolved is True

    def test_response_with_auto_detect_pending(self):
        """Test response with auto_detect, domain not yet resolved."""
        response_data = {
            "id": "12345678-1234-1234-1234-123456789012",
            "tenant_id": "12345678-1234-1234-1234-123456789012",
            "created_by_user_id": "user123",
            "name": "Test Job",
            "start_url": "https://example.com",
            "allowed_domains": ["example.com"],
            "crawl_depth": 2,
            "max_pages": 100,
            "crawl_speed": 1.0,
            "respect_robots_txt": True,
            "use_llm_extraction": True,
            "custom_settings": {},
            "status": "running",
            "pages_crawled": 5,
            "entities_extracted": 0,
            "errors_count": 0,
            "created_at": "2025-12-15T00:00:00Z",
            "updated_at": "2025-12-15T00:00:00Z",
            "extraction_strategy": "auto_detect",
            "content_domain": None,  # Not yet classified
            "classification_confidence": None,
            "classification_sample_size": 3,
            "uses_adaptive_extraction": True,
            "is_domain_resolved": False,  # Waiting for classification
        }
        response = ScrapingJobResponse(**response_data)
        assert response.extraction_strategy == "auto_detect"
        assert response.content_domain is None
        assert response.classification_sample_size == 3
        assert response.uses_adaptive_extraction is True
        assert response.is_domain_resolved is False

    def test_response_with_auto_detect_resolved(self):
        """Test response with auto_detect after classification."""
        response_data = {
            "id": "12345678-1234-1234-1234-123456789012",
            "tenant_id": "12345678-1234-1234-1234-123456789012",
            "created_by_user_id": "user123",
            "name": "Test Job",
            "start_url": "https://example.com",
            "allowed_domains": ["example.com"],
            "crawl_depth": 2,
            "max_pages": 100,
            "crawl_speed": 1.0,
            "respect_robots_txt": True,
            "use_llm_extraction": True,
            "custom_settings": {},
            "status": "running",
            "pages_crawled": 10,
            "entities_extracted": 25,
            "errors_count": 0,
            "created_at": "2025-12-15T00:00:00Z",
            "updated_at": "2025-12-15T00:00:00Z",
            "extraction_strategy": "auto_detect",
            "content_domain": "literature_fiction",
            "classification_confidence": 0.92,
            "classification_sample_size": 3,
            "uses_adaptive_extraction": True,
            "is_domain_resolved": True,
        }
        response = ScrapingJobResponse(**response_data)
        assert response.content_domain == "literature_fiction"
        assert response.classification_confidence == 0.92
        assert response.is_domain_resolved is True

    def test_response_with_manual_strategy(self):
        """Test response with manual domain selection."""
        response_data = {
            "id": "12345678-1234-1234-1234-123456789012",
            "tenant_id": "12345678-1234-1234-1234-123456789012",
            "created_by_user_id": "user123",
            "name": "Test Job",
            "start_url": "https://example.com",
            "allowed_domains": ["example.com"],
            "crawl_depth": 2,
            "max_pages": 100,
            "crawl_speed": 1.0,
            "respect_robots_txt": True,
            "use_llm_extraction": True,
            "custom_settings": {},
            "status": "pending",
            "pages_crawled": 0,
            "entities_extracted": 0,
            "errors_count": 0,
            "created_at": "2025-12-15T00:00:00Z",
            "updated_at": "2025-12-15T00:00:00Z",
            "extraction_strategy": "manual",
            "content_domain": "technical_documentation",
            "classification_confidence": None,  # Not used for manual
            "classification_sample_size": 1,  # Default, not used
            "uses_adaptive_extraction": True,
            "is_domain_resolved": True,
        }
        response = ScrapingJobResponse(**response_data)
        assert response.extraction_strategy == "manual"
        assert response.content_domain == "technical_documentation"
        assert response.classification_confidence is None
        assert response.uses_adaptive_extraction is True
        assert response.is_domain_resolved is True


class TestScrapingJobSummaryAdaptiveFields:
    """Tests for ScrapingJobSummary adaptive extraction fields."""

    def test_summary_includes_extraction_strategy(self):
        """Test summary includes extraction strategy for filtering."""
        summary_data = {
            "id": "12345678-1234-1234-1234-123456789012",
            "name": "Test Job",
            "start_url": "https://example.com",
            "status": "running",
            "stage": "extracting",
            "pages_crawled": 10,
            "entities_extracted": 25,
            "extraction_strategy": "auto_detect",
            "content_domain": "literature_fiction",
            "uses_adaptive_extraction": True,
            "created_at": "2025-12-15T00:00:00Z",
        }
        summary = ScrapingJobSummary(**summary_data)
        assert summary.extraction_strategy == "auto_detect"
        assert summary.content_domain == "literature_fiction"
        assert summary.uses_adaptive_extraction is True

    def test_summary_legacy_defaults(self):
        """Test summary defaults for legacy jobs."""
        summary_data = {
            "id": "12345678-1234-1234-1234-123456789012",
            "name": "Test Job",
            "start_url": "https://example.com",
            "status": "pending",
            "pages_crawled": 0,
            "entities_extracted": 0,
            "created_at": "2025-12-15T00:00:00Z",
            # Not providing adaptive fields - should use defaults
        }
        summary = ScrapingJobSummary(**summary_data)
        assert summary.extraction_strategy == "legacy"
        assert summary.content_domain is None
        assert summary.uses_adaptive_extraction is False
