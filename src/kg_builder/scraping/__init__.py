"""
Scrapy integration for web scraping.

This package provides:
- Scrapy settings configuration
- Tenant-aware spider base class
- Item pipelines for processing scraped content
- Spider runner for Celery integration
"""

from kg_builder.scraping.items import ScrapedPageItem
from kg_builder.scraping.runner import run_spider_for_job

__all__ = [
    "ScrapedPageItem",
    "run_spider_for_job",
]
