"""
Scrapy item definitions for scraped content.

Items define the structure of data extracted by spiders
and processed by pipelines.
"""

import scrapy


class ScrapedPageItem(scrapy.Item):
    """
    Item representing a scraped web page.

    This item captures all metadata and content from a crawled page
    for storage and further processing.
    """

    # Identifiers
    job_id = scrapy.Field()
    tenant_id = scrapy.Field()
    url = scrapy.Field()
    canonical_url = scrapy.Field()

    # Content
    html_content = scrapy.Field()
    text_content = scrapy.Field()

    # Metadata
    title = scrapy.Field()
    meta_description = scrapy.Field()
    meta_keywords = scrapy.Field()

    # Structured data
    schema_org_data = scrapy.Field()
    open_graph_data = scrapy.Field()

    # HTTP info
    http_status = scrapy.Field()
    content_type = scrapy.Field()
    response_headers = scrapy.Field()

    # Crawl info
    crawled_at = scrapy.Field()
    depth = scrapy.Field()
    content_hash = scrapy.Field()

    # Links found on page
    links = scrapy.Field()

    # Database ID (set by DatabaseStoragePipeline after insert)
    page_id = scrapy.Field()


class LinkItem(scrapy.Item):
    """
    Item representing a discovered link.

    Used for tracking link relationships between pages.
    """

    source_url = scrapy.Field()
    target_url = scrapy.Field()
    link_text = scrapy.Field()
    link_type = scrapy.Field()  # internal, external, etc.
