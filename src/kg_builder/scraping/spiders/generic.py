"""
Generic web spider for crawling any website.

This spider implements a general-purpose crawling strategy
suitable for most websites.
"""

import logging
from typing import Any

from scrapy.http import Response

from kg_builder.scraping.items import ScrapedPageItem
from kg_builder.scraping.spiders.base import TenantAwareSpider

logger = logging.getLogger(__name__)


class GenericSpider(TenantAwareSpider):
    """
    Generic spider for crawling websites.

    Features:
    - Follows links within allowed domains
    - Extracts page content and metadata
    - Respects robots.txt and rate limits
    - Supports URL patterns for filtering
    """

    name = "generic"

    # Custom rules for link extraction
    link_extractor_tags = ["a", "area"]
    link_extractor_attrs = ["href"]

    def parse(self, response: Response, **kwargs) -> Any:
        """
        Parse a crawled page.

        Args:
            response: HTTP response from Scrapy

        Yields:
            ScrapedPageItem and follow-up requests
        """
        # Check page limit
        if self.pages_crawled >= self.max_pages:
            logger.info(
                f"Reached max pages limit ({self.max_pages}), stopping crawl",
                extra={"job_id": str(self.job_id)},
            )
            return

        # Only process HTML responses
        content_type = response.headers.get(
            "Content-Type", b""
        ).decode("utf-8", errors="ignore")

        if "text/html" not in content_type.lower():
            logger.debug(
                f"Skipping non-HTML response: {response.url} ({content_type})",
            )
            return

        # Extract page data
        item = self._extract_page_data(response)
        if item:
            # Enrich with additional metadata
            item = self._enrich_item(item, response)
            yield item
            self.pages_crawled += 1

            # Log progress
            if self.pages_crawled % 10 == 0:
                logger.info(
                    f"Progress: {self.pages_crawled}/{self.max_pages} pages",
                    extra={
                        "job_id": str(self.job_id),
                        "pages_crawled": self.pages_crawled,
                    },
                )

            # Update progress callback
            if self.progress_callback:
                self.progress_callback(self.pages_crawled, 0, 0)

        # Follow links
        yield from self._follow_links(response)

    def _enrich_item(
        self,
        item: ScrapedPageItem,
        response: Response,
    ) -> ScrapedPageItem:
        """
        Enrich item with additional extracted data.

        Args:
            item: Page item to enrich
            response: HTTP response

        Returns:
            Enriched ScrapedPageItem
        """
        # Extract additional metadata
        item["links"] = self._extract_links(response)

        # Extract Open Graph metadata (basic)
        og_data = {}
        for og_tag in response.xpath("//meta[starts-with(@property, 'og:')]"):
            prop = og_tag.xpath("@property").get()
            content = og_tag.xpath("@content").get()
            if prop and content:
                og_data[prop.replace("og:", "")] = content

        if og_data and not item.get("open_graph_data"):
            item["open_graph_data"] = og_data

        return item

    def _extract_links(self, response: Response) -> list[dict]:
        """
        Extract links from the page.

        Args:
            response: HTTP response

        Returns:
            List of link dictionaries
        """
        links = []
        seen = set()

        for tag in self.link_extractor_tags:
            for attr in self.link_extractor_attrs:
                for link_elem in response.xpath(f"//{tag}[@{attr}]"):
                    href = link_elem.xpath(f"@{attr}").get()
                    if not href:
                        continue

                    # Build absolute URL
                    url = response.urljoin(href)

                    # Skip duplicates
                    if url in seen:
                        continue
                    seen.add(url)

                    # Get link text
                    text = link_elem.xpath("normalize-space(.)").get()

                    # Determine link type
                    link_type = self._classify_link(url, response.url)

                    links.append({
                        "url": url,
                        "text": text,
                        "type": link_type,
                    })

        return links

    def _classify_link(self, link_url: str, page_url: str) -> str:
        """
        Classify a link as internal, external, or other.

        Args:
            link_url: URL of the link
            page_url: URL of the current page

        Returns:
            Link type string
        """
        from urllib.parse import urlparse

        link_parsed = urlparse(link_url)
        page_parsed = urlparse(page_url)

        # Same domain
        if link_parsed.netloc == page_parsed.netloc:
            return "internal"

        # In allowed domains
        if link_parsed.netloc in self.allowed_domains:
            return "internal"

        # External
        return "external"


class SitemapSpider(TenantAwareSpider):
    """
    Spider that crawls based on sitemap.xml.

    Useful for sites with well-structured sitemaps.
    """

    name = "sitemap"

    sitemap_urls: list[str] = []
    sitemap_rules: list[tuple] = []

    def __init__(self, sitemap_urls: list[str] = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sitemap_urls = sitemap_urls or []

        # Add sitemap URL from start_url domain
        if self.start_url and not self.sitemap_urls:
            from urllib.parse import urlparse
            parsed = urlparse(self.start_url)
            self.sitemap_urls = [
                f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
            ]

    @property
    def start_urls(self) -> list[str]:
        """Return sitemap URLs."""
        return self.sitemap_urls

    def parse(self, response: Response, **kwargs) -> Any:
        """
        Parse sitemap or regular page.

        Args:
            response: HTTP response

        Yields:
            Items or requests
        """
        content_type = response.headers.get(
            "Content-Type", b""
        ).decode("utf-8", errors="ignore")

        # Check if this is a sitemap
        if "xml" in content_type or response.url.endswith(".xml"):
            yield from self._parse_sitemap(response)
        else:
            yield from super().parse(response, **kwargs)

    def _parse_sitemap(self, response: Response):
        """
        Parse sitemap XML.

        Args:
            response: HTTP response

        Yields:
            Requests for discovered URLs
        """
        # Remove namespace prefixes for easier parsing
        body = response.text
        body = body.replace('xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"', '')

        from scrapy import Selector
        selector = Selector(text=body)

        # Check for sitemap index
        sitemaps = selector.xpath("//sitemap/loc/text()").getall()
        for sitemap_url in sitemaps:
            yield response.follow(sitemap_url, callback=self._parse_sitemap)

        # Extract URLs
        urls = selector.xpath("//url/loc/text()").getall()
        for url in urls:
            if self._should_follow_url(url):
                yield response.follow(
                    url,
                    callback=self.parse,
                    meta={"depth": 0},
                )

        logger.info(
            f"Parsed sitemap: {len(urls)} URLs found",
            extra={
                "url": response.url,
                "urls_found": len(urls),
                "sitemaps_found": len(sitemaps),
            },
        )
