"""
Scrapy spiders for web crawling.

This package contains spider implementations:
- TenantAwareSpider: Base class for tenant-isolated scraping
- GenericSpider: Generic website crawler
"""

from kg_builder.scraping.spiders.base import TenantAwareSpider
from kg_builder.scraping.spiders.generic import GenericSpider

__all__ = [
    "GenericSpider",
    "TenantAwareSpider",
]
