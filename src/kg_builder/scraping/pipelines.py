"""
Scrapy pipelines for processing scraped items.

Pipelines handle:
- Content extraction and cleaning
- Schema.org data extraction
- Database storage
- Extraction task queueing
"""

import hashlib
import logging
from datetime import UTC, datetime
from uuid import UUID

from scrapy import Spider

from kg_builder.scraping.items import ScrapedPageItem

logger = logging.getLogger(__name__)


class ContentExtractionPipeline:
    """
    Pipeline for extracting and cleaning page content.

    Extracts text content from HTML and calculates content hash.
    """

    def process_item(self, item: ScrapedPageItem, spider: Spider) -> ScrapedPageItem:
        """Extract text content and compute hash."""
        if not isinstance(item, ScrapedPageItem):
            return item

        html_content = item.get("html_content", "")

        # Extract text content from HTML
        if html_content and not item.get("text_content"):
            item["text_content"] = self._extract_text(html_content)

        # Compute content hash for deduplication
        if html_content and not item.get("content_hash"):
            item["content_hash"] = self._compute_hash(html_content)

        return item

    def _extract_text(self, html: str) -> str:
        """Extract readable text from HTML."""
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "lxml")

            # Remove script and style elements
            for element in soup(["script", "style", "nav", "footer", "header"]):
                element.decompose()

            # Get text
            text = soup.get_text(separator=" ", strip=True)

            # Clean up whitespace
            import re
            text = re.sub(r"\s+", " ", text)

            return text.strip()

        except Exception as e:
            logger.warning(f"Failed to extract text: {e}")
            return ""

    def _compute_hash(self, content: str) -> str:
        """Compute SHA-256 hash of content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


class SchemaOrgExtractionPipeline:
    """
    Pipeline for extracting Schema.org and Open Graph data.

    Uses extruct to parse JSON-LD, Microdata, and RDFa.
    """

    def process_item(self, item: ScrapedPageItem, spider: Spider) -> ScrapedPageItem:
        """Extract structured data from HTML."""
        if not isinstance(item, ScrapedPageItem):
            return item

        html_content = item.get("html_content", "")
        url = item.get("url", "")

        if not html_content:
            return item

        try:
            import extruct

            # Extract all structured data
            data = extruct.extract(
                html_content,
                base_url=url,
                syntaxes=["json-ld", "microdata", "opengraph", "rdfa"],
            )

            # Store Schema.org data (JSON-LD + Microdata)
            schema_org = data.get("json-ld", []) + data.get("microdata", [])
            if schema_org:
                item["schema_org_data"] = schema_org

            # Store Open Graph data
            if data.get("opengraph"):
                item["open_graph_data"] = data["opengraph"][0] if data["opengraph"] else {}

            logger.debug(
                f"Extracted structured data from {url}",
                extra={
                    "url": url,
                    "schema_org_count": len(schema_org),
                    "has_open_graph": bool(data.get("opengraph")),
                },
            )

        except Exception as e:
            logger.warning(f"Failed to extract structured data from {url}: {e}")
            item["schema_org_data"] = []
            item["open_graph_data"] = {}

        return item


class DatabaseStoragePipeline:
    """
    Pipeline for storing scraped pages in the database.

    Stores page content and metadata in PostgreSQL.
    """

    def __init__(self):
        self._session = None

    def open_spider(self, spider: Spider) -> None:
        """Open database session when spider starts."""
        from sqlalchemy import text

        from kg_builder.db import SyncSessionLocal

        self._session = SyncSessionLocal()

        # Set tenant context
        tenant_id = getattr(spider, "tenant_id", None)
        if tenant_id:
            self._session.execute(
                text("SET app.current_tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )

        logger.info(
            "Database storage pipeline opened",
            extra={"tenant_id": tenant_id},
        )

    def close_spider(self, spider: Spider) -> None:
        """Close database session when spider finishes."""
        if self._session:
            try:
                self._session.commit()
            except Exception as e:
                logger.error(f"Failed to commit final changes: {e}")
                self._session.rollback()
            finally:
                self._session.close()
                self._session = None

        logger.info("Database storage pipeline closed")

    def process_item(self, item: ScrapedPageItem, spider: Spider) -> ScrapedPageItem:
        """Store scraped page in database."""
        if not isinstance(item, ScrapedPageItem):
            return item

        if not self._session:
            logger.error("Database session not available")
            return item

        try:
            from kg_builder.models.scraped_page import ScrapedPage

            # Create scraped page record
            page = ScrapedPage(
                tenant_id=UUID(str(item["tenant_id"])),
                job_id=UUID(str(item["job_id"])),
                url=item["url"],
                canonical_url=item.get("canonical_url"),
                content_hash=item.get("content_hash", ""),
                html_content=item.get("html_content", ""),
                text_content=item.get("text_content", ""),
                title=item.get("title"),
                meta_description=item.get("meta_description"),
                meta_keywords=item.get("meta_keywords"),
                schema_org_data=item.get("schema_org_data", []),
                open_graph_data=item.get("open_graph_data", {}),
                http_status=item.get("http_status", 200),
                content_type=item.get("content_type", "text/html"),
                response_headers=item.get("response_headers", {}),
                crawled_at=item.get("crawled_at", datetime.now(UTC)),
                depth=item.get("depth", 0),
                extraction_status="pending",
            )

            self._session.add(page)
            self._session.flush()  # Get the ID
            self._session.commit()  # Commit so extraction task can see the page

            # Store page ID in item for downstream processing
            item["page_id"] = str(page.id)

            logger.debug(
                f"Stored scraped page: {item['url']}",
                extra={
                    "page_id": str(page.id),
                    "url": item["url"],
                    "tenant_id": str(item["tenant_id"]),
                },
            )

        except Exception as e:
            logger.error(
                f"Failed to store scraped page: {e}",
                extra={"url": item.get("url")},
                exc_info=True,
            )
            self._session.rollback()

        return item


class ExtractionQueuePipeline:
    """
    Pipeline for queueing pages for entity extraction.

    Queues Celery tasks for async entity extraction.
    """

    def process_item(self, item: ScrapedPageItem, spider: Spider) -> ScrapedPageItem:
        """Queue page for entity extraction."""
        if not isinstance(item, ScrapedPageItem):
            return item

        page_id = item.get("page_id")
        tenant_id = item.get("tenant_id")

        if not page_id or not tenant_id:
            logger.warning("Missing page_id or tenant_id, skipping extraction queue")
            return item

        try:
            # Host application injects a dispatcher; see kg_builder.scraping.hooks
            from kg_builder.scraping.hooks import get_extraction_dispatcher

            get_extraction_dispatcher()(page_id, str(tenant_id))

            logger.debug(
                f"Queued extraction for page: {page_id}",
                extra={
                    "page_id": page_id,
                    "url": item.get("url"),
                    "tenant_id": str(tenant_id),
                },
            )

        except Exception as e:
            logger.error(
                f"Failed to queue extraction task: {e}",
                extra={"page_id": page_id},
            )

        return item
