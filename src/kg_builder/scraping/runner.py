"""
Scrapy runner for executing spiders from Celery tasks.

Provides a clean interface for running Scrapy spiders
in a background task context.
"""

import logging
from collections.abc import Callable

from scrapy.crawler import CrawlerProcess

from kg_builder.models.scraping_job import ScrapingJob
from kg_builder.scraping.settings import get_scrapy_settings
from kg_builder.scraping.spiders import GenericSpider

logger = logging.getLogger(__name__)


def run_spider_for_job(
    job: ScrapingJob,
    tenant_id: str,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> dict:
    """
    Run a Scrapy spider for a scraping job.

    Args:
        job: ScrapingJob instance with configuration
        tenant_id: UUID of the tenant
        on_progress: Callback for progress updates (pages, entities, errors)

    Returns:
        dict: Execution summary with stats
    """
    logger.info(
        f"Starting spider for job {job.id}",
        extra={
            "job_id": str(job.id),
            "tenant_id": tenant_id,
            "start_url": job.start_url,
        },
    )

    # Get Scrapy settings for this job
    settings = get_scrapy_settings(job)

    # Create crawler process
    process = CrawlerProcess(settings=settings)

    # Configure spider
    spider_kwargs = {
        "job_id": str(job.id),
        "tenant_id": tenant_id,
        "start_url": job.start_url,
        "allowed_domains": job.allowed_domains,
        "url_patterns": job.url_patterns,
        "excluded_patterns": job.excluded_patterns,
        "crawl_depth": job.crawl_depth,
        "max_pages": job.max_pages,
        "use_llm_extraction": job.use_llm_extraction,
        "progress_callback": on_progress,
    }

    # Add spider to crawler
    process.crawl(GenericSpider, **spider_kwargs)

    try:
        # Run the spider (blocking)
        process.start()

        # Get stats from crawler
        stats = {}
        for crawler in process.crawlers:
            stats = crawler.stats.get_stats()

        summary = {
            "pages_crawled": stats.get("item_scraped_count", 0),
            "requests_made": stats.get("downloader/request_count", 0),
            "responses_received": stats.get("downloader/response_count", 0),
            "errors": stats.get("log_count/ERROR", 0),
            "finish_reason": stats.get("finish_reason", "unknown"),
        }

        logger.info(
            f"Spider completed for job {job.id}",
            extra={
                "job_id": str(job.id),
                **summary,
            },
        )

        return summary

    except Exception as e:
        logger.exception(
            f"Spider failed for job {job.id}: {e}",
            extra={"job_id": str(job.id)},
        )
        raise


def run_spider_async(
    job: ScrapingJob,
    tenant_id: str,
    on_progress: Callable[[int, int, int], None] | None = None,
):
    """
    Run spider asynchronously using Twisted deferred.

    This is useful when you need non-blocking execution.

    Args:
        job: ScrapingJob instance
        tenant_id: UUID of the tenant
        on_progress: Progress callback

    Returns:
        Deferred that resolves when spider completes
    """
    from scrapy.crawler import CrawlerRunner

    settings = get_scrapy_settings(job)
    runner = CrawlerRunner(settings=settings)

    spider_kwargs = {
        "job_id": str(job.id),
        "tenant_id": tenant_id,
        "start_url": job.start_url,
        "allowed_domains": job.allowed_domains,
        "url_patterns": job.url_patterns,
        "excluded_patterns": job.excluded_patterns,
        "crawl_depth": job.crawl_depth,
        "max_pages": job.max_pages,
        "use_llm_extraction": job.use_llm_extraction,
        "progress_callback": on_progress,
    }

    deferred = runner.crawl(GenericSpider, **spider_kwargs)
    return deferred


class SpiderMonitor:
    """
    Monitor for tracking spider execution.

    Provides callbacks and hooks for monitoring spider progress.
    """

    def __init__(self, job_id: str, tenant_id: str):
        self.job_id = job_id
        self.tenant_id = tenant_id
        self.pages_crawled = 0
        self.entities_extracted = 0
        self.errors_count = 0

    def on_page_crawled(self, url: str, status: int) -> None:
        """Called when a page is crawled."""
        self.pages_crawled += 1
        logger.debug(
            f"Page crawled: {url} ({status})",
            extra={
                "job_id": self.job_id,
                "url": url,
                "status": status,
                "pages_crawled": self.pages_crawled,
            },
        )

    def on_entity_extracted(self, entity_type: str, name: str) -> None:
        """Called when an entity is extracted."""
        self.entities_extracted += 1
        logger.debug(
            f"Entity extracted: {entity_type}/{name}",
            extra={
                "job_id": self.job_id,
                "entity_type": entity_type,
                "entity_name": name,
                "entities_extracted": self.entities_extracted,
            },
        )

    def on_error(self, url: str, error: str) -> None:
        """Called when an error occurs."""
        self.errors_count += 1
        logger.warning(
            f"Crawl error: {url} - {error}",
            extra={
                "job_id": self.job_id,
                "url": url,
                "error": error,
                "errors_count": self.errors_count,
            },
        )

    def get_progress(self) -> tuple[int, int, int]:
        """Get current progress."""
        return (self.pages_crawled, self.entities_extracted, self.errors_count)
