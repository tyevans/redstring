"""
Base spider class with tenant awareness.

Provides common functionality for all Knowledge Mapper spiders:
- Tenant context management
- Job configuration loading
- Progress tracking
- Domain restrictions
"""

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import scrapy
from scrapy.http import Response

from kg_builder.scraping.items import ScrapedPageItem

logger = logging.getLogger(__name__)

# Wikipedia namespace prefixes that indicate non-article pages
# These are MediaWiki namespaces that exist across all Wikipedia language editions
WIKIPEDIA_NON_ARTICLE_PREFIXES = frozenset(
    {
        "Talk:",
        "User:",
        "User_talk:",
        "Wikipedia:",
        "Wikipedia_talk:",
        "WP:",  # Shortcut for Wikipedia:
        "Project:",  # Alias for Wikipedia:
        "Project_talk:",
        "Template:",
        "Template_talk:",
        "Category:",
        "Category_talk:",
        "Special:",
        "File:",
        "File_talk:",
        "Image:",  # Legacy alias for File:
        "Image_talk:",
        "Media:",
        "Help:",
        "Help_talk:",
        "Module:",
        "Module_talk:",
        "Portal:",
        "Portal_talk:",
        "Draft:",
        "Draft_talk:",
        "TimedText:",
        "TimedText_talk:",
        "MediaWiki:",
        "MediaWiki_talk:",
        "Book:",
        "Book_talk:",
        "Education_Program:",
        "Education_Program_talk:",
        "Gadget:",
        "Gadget_talk:",
        "Gadget_definition:",
        "Gadget_definition_talk:",
    }
)

# Query parameters that indicate non-article views (edit mode, history, etc.)
WIKIPEDIA_NON_ARTICLE_PARAMS = frozenset(
    {
        "action",  # action=edit, action=history, etc.
        "oldid",  # Specific revision
        "diff",  # Diff between revisions
        "curid",  # Page ID redirect
        "printable",
        "mobileaction",
        "veaction",  # Visual editor
    }
)


class TenantAwareSpider(scrapy.Spider):
    """
    Base spider class with tenant isolation.

    All Knowledge Mapper spiders should inherit from this class
    to ensure proper tenant context and configuration handling.
    """

    name = "tenant_aware"

    # These will be set from job configuration
    job_id: UUID | None = None
    tenant_id: UUID | None = None
    start_url: str | None = None
    allowed_domains: list[str] = []
    url_patterns: list[str] = []
    excluded_patterns: list[str] = []
    crawl_depth: int = 2
    max_pages: int = 100
    use_llm_extraction: bool = True

    # Progress tracking
    pages_crawled: int = 0
    progress_callback: Callable[[int, int, int], None] | None = None

    def __init__(
        self,
        job_id: str = None,
        tenant_id: str = None,
        start_url: str = None,
        allowed_domains: list[str] = None,
        url_patterns: list[str] = None,
        excluded_patterns: list[str] = None,
        crawl_depth: int = 2,
        max_pages: int = 100,
        use_llm_extraction: bool = True,
        progress_callback: Callable[[int, int, int], None] = None,
        *args,
        **kwargs,
    ):
        """
        Initialize spider with job configuration.

        Args:
            job_id: UUID of the scraping job
            tenant_id: UUID of the tenant
            start_url: URL to begin crawling from
            allowed_domains: List of domains to stay within
            url_patterns: Regex patterns for URLs to include
            excluded_patterns: Regex patterns for URLs to exclude
            crawl_depth: Maximum link depth to follow
            max_pages: Maximum pages to scrape
            use_llm_extraction: Whether to use LLM for extraction
            progress_callback: Callback for progress updates
        """
        super().__init__(*args, **kwargs)

        self.job_id = UUID(job_id) if job_id else None
        self.tenant_id = UUID(tenant_id) if tenant_id else None
        self.start_url = start_url
        self.allowed_domains = allowed_domains or []
        self.url_patterns = url_patterns or []
        self.excluded_patterns = excluded_patterns or []
        self.crawl_depth = crawl_depth
        self.max_pages = max_pages
        self.use_llm_extraction = use_llm_extraction
        self.progress_callback = progress_callback

        # Compile patterns
        self._url_patterns = [re.compile(p) for p in self.url_patterns]
        self._excluded_patterns = [re.compile(p) for p in self.excluded_patterns]

        # Extract domain from start_url if not specified
        if not self.allowed_domains and self.start_url:
            parsed = urlparse(self.start_url)
            if parsed.netloc:
                self.allowed_domains = [parsed.netloc]

        logger.info(
            f"Spider initialized for job {job_id}",
            extra={
                "job_id": str(job_id),
                "tenant_id": str(tenant_id),
                "start_url": start_url,
                "allowed_domains": self.allowed_domains,
                "crawl_depth": crawl_depth,
                "max_pages": max_pages,
            },
        )

    @property
    def start_urls(self) -> list[str]:
        """Return starting URLs."""
        if self.start_url:
            return [self.start_url]
        return []

    def parse(self, response: Response, **kwargs) -> Any:
        """
        Default parse method for processing responses.

        Override in subclasses for custom parsing logic.

        Args:
            response: HTTP response from Scrapy

        Yields:
            ScrapedPageItem or Request objects
        """
        # Check if we've hit the page limit
        if self.pages_crawled >= self.max_pages:
            logger.info(f"Reached max pages limit ({self.max_pages})")
            return

        # Extract page data
        item = self._extract_page_data(response)
        if item:
            yield item
            self.pages_crawled += 1

            # Update progress
            if self.progress_callback:
                self.progress_callback(self.pages_crawled, 0, 0)

        # Extract and follow links
        yield from self._follow_links(response)

    def _extract_page_data(self, response: Response) -> ScrapedPageItem | None:
        """
        Extract page data from response.

        Args:
            response: HTTP response

        Returns:
            ScrapedPageItem or None
        """
        try:
            item = ScrapedPageItem()

            # Identifiers
            item["job_id"] = str(self.job_id)
            item["tenant_id"] = str(self.tenant_id)
            item["url"] = response.url

            # Extract canonical URL
            canonical = response.xpath("//link[@rel='canonical']/@href").get()
            item["canonical_url"] = canonical or response.url

            # Content
            item["html_content"] = response.text
            item["text_content"] = ""  # Will be extracted by pipeline

            # Metadata
            item["title"] = response.xpath("//title/text()").get()
            item["meta_description"] = response.xpath("//meta[@name='description']/@content").get()
            item["meta_keywords"] = response.xpath("//meta[@name='keywords']/@content").get()

            # HTTP info
            item["http_status"] = response.status
            item["content_type"] = response.headers.get("Content-Type", b"text/html").decode(
                "utf-8", errors="ignore"
            )
            item["response_headers"] = dict(response.headers.to_unicode_dict())

            # Crawl info
            item["crawled_at"] = datetime.now(UTC)
            item["depth"] = response.meta.get("depth", 0)

            logger.debug(
                f"Extracted page data: {response.url}",
                extra={
                    "url": response.url,
                    "status": response.status,
                    "depth": item["depth"],
                },
            )

            return item

        except Exception as e:
            logger.error(
                f"Failed to extract page data from {response.url}: {e}",
                exc_info=True,
            )
            return None

    def _follow_links(self, response: Response):
        """
        Extract and follow links from the page.

        Args:
            response: HTTP response

        Yields:
            Request objects for discovered links
        """
        current_depth = response.meta.get("depth", 0)

        # Don't follow links if at max depth
        if current_depth >= self.crawl_depth:
            return

        # Extract all links
        links = response.xpath("//a/@href").getall()

        for link in links:
            # Build absolute URL
            url = response.urljoin(link)

            # Check if URL should be followed
            if self._should_follow_url(url):
                yield response.follow(
                    link,
                    callback=self.parse,
                    meta={"depth": current_depth + 1},
                    errback=self._handle_error,
                )

    def _should_follow_url(self, url: str) -> bool:
        """
        Check if a URL should be followed.

        Args:
            url: URL to check

        Returns:
            True if URL should be followed
        """
        # Parse URL
        parsed = urlparse(url)

        # Check domain
        if self.allowed_domains:
            if parsed.netloc not in self.allowed_domains:
                return False

        # Check excluded patterns
        for pattern in self._excluded_patterns:
            if pattern.search(url):
                return False

        # Check include patterns (if specified)
        if self._url_patterns:
            matched = any(p.search(url) for p in self._url_patterns)
            if not matched:
                return False

        # Skip non-HTTP URLs
        if parsed.scheme not in ("http", "https"):
            return False

        # Skip common non-content URLs
        skip_extensions = {
            ".css",
            ".js",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".svg",
            ".ico",
            ".woff",
            ".woff2",
            ".ttf",
            ".eot",
            ".pdf",
            ".zip",
            ".tar",
            ".gz",
            ".mp3",
            ".mp4",
            ".avi",
            ".mov",
        }
        if any(parsed.path.lower().endswith(ext) for ext in skip_extensions):
            return False

        # Apply Wikipedia-specific filtering for Wikipedia domains
        if self._is_wikipedia_domain(parsed.netloc):
            if not self._is_wikipedia_article_url(parsed):
                logger.debug(
                    f"Skipping non-article Wikipedia URL: {url}",
                    extra={"url": url, "job_id": str(self.job_id)},
                )
                return False

        return True

    def _is_wikipedia_domain(self, netloc: str) -> bool:
        """
        Check if a domain is a Wikipedia or Wikimedia site.

        Matches patterns like:
        - en.wikipedia.org, de.wikipedia.org, etc.
        - en.m.wikipedia.org (mobile)
        - commons.wikimedia.org
        - en.wiktionary.org, en.wikiquote.org, etc.

        Args:
            netloc: The network location (domain) from a parsed URL

        Returns:
            True if this is a Wikipedia/Wikimedia domain
        """
        netloc_lower = netloc.lower()
        wikimedia_domains = (
            "wikipedia.org",
            "wikimedia.org",
            "wiktionary.org",
            "wikiquote.org",
            "wikibooks.org",
            "wikisource.org",
            "wikinews.org",
            "wikiversity.org",
            "wikivoyage.org",
            "wikidata.org",
            "mediawiki.org",
        )
        return any(netloc_lower.endswith(domain) for domain in wikimedia_domains)

    def _is_wikipedia_article_url(self, parsed) -> bool:
        """
        Check if a Wikipedia URL points to an actual article.

        Filters out:
        - Non-article namespaces (Talk:, User:, Special:, etc.)
        - Edit/history/diff views
        - Other non-content pages

        Args:
            parsed: ParseResult from urlparse()

        Returns:
            True if URL appears to be an article page
        """
        path = parsed.path

        # Must be a wiki page URL
        if not path.startswith("/wiki/"):
            # Also allow /w/index.php article views, but filter most
            if path.startswith("/w/"):
                return False
            # Allow root and other paths through (they'll be filtered elsewhere)
            return True

        # Extract the page title from the path
        # /wiki/Article_Title -> Article_Title
        page_title = path[6:]  # Remove "/wiki/" prefix

        # URL decode is not needed here - we check the encoded form
        # which uses underscores (Talk:Page_title vs Talk:Page title)

        # Check for non-article namespace prefixes
        for prefix in WIKIPEDIA_NON_ARTICLE_PREFIXES:
            if page_title.startswith(prefix):
                return False

        # Check query parameters for non-article views
        query_params = parse_qs(parsed.query)
        for param in WIKIPEDIA_NON_ARTICLE_PARAMS:
            if param in query_params:
                return False

        # Looks like an article!
        return True

    def _handle_error(self, failure):
        """Handle request failures."""
        logger.error(
            f"Request failed: {failure.request.url} - {failure.value}",
            extra={
                "url": failure.request.url,
                "error": str(failure.value),
                "job_id": str(self.job_id),
            },
        )
