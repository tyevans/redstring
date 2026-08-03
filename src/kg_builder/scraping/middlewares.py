"""
Scrapy middleware classes for tenant-aware scraping.

Middlewares handle cross-cutting concerns like:
- Tenant context management
- User agent rotation
- Request logging
- Rate limiting
"""

import logging
import random
from typing import Optional

from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.http import Request, Response

logger = logging.getLogger(__name__)

# Common user agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


class TenantContextMiddleware:
    """
    Spider middleware to manage tenant context during crawling.

    Ensures each request and response is processed with the correct
    tenant context for multi-tenant isolation.
    """

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        """Create middleware instance from crawler."""
        middleware = cls()
        crawler.signals.connect(
            middleware.spider_opened, signal=signals.spider_opened
        )
        crawler.signals.connect(
            middleware.spider_closed, signal=signals.spider_closed
        )
        return middleware

    def spider_opened(self, spider: Spider) -> None:
        """Handle spider opened signal."""
        tenant_id = getattr(spider, "tenant_id", None)
        if tenant_id:
            logger.info(
                f"Spider opened with tenant context: {tenant_id}",
                extra={"tenant_id": tenant_id, "spider": spider.name},
            )

    def spider_closed(self, spider: Spider, reason: str) -> None:
        """Handle spider closed signal."""
        tenant_id = getattr(spider, "tenant_id", None)
        logger.info(
            f"Spider closed: {reason}",
            extra={"tenant_id": tenant_id, "spider": spider.name, "reason": reason},
        )

    def process_spider_input(self, response: Response, spider: Spider):
        """Process response before spider callback."""
        # Log response received
        logger.debug(
            f"Response received: {response.url} ({response.status})",
            extra={
                "url": response.url,
                "status": response.status,
                "tenant_id": getattr(spider, "tenant_id", None),
            },
        )
        return None

    def process_spider_output(self, response: Response, result, spider: Spider):
        """Process items/requests output by spider."""
        for item in result:
            # Add tenant context to items
            if hasattr(item, "__setitem__"):
                if "tenant_id" not in item or not item.get("tenant_id"):
                    item["tenant_id"] = getattr(spider, "tenant_id", None)
                if "job_id" not in item or not item.get("job_id"):
                    item["job_id"] = getattr(spider, "job_id", None)
            yield item

    def process_spider_exception(
        self,
        response: Response,
        exception: Exception,
        spider: Spider,
    ):
        """Process spider exceptions."""
        logger.error(
            f"Spider exception for {response.url}: {exception}",
            extra={
                "url": response.url,
                "exception": str(exception),
                "tenant_id": getattr(spider, "tenant_id", None),
            },
            exc_info=True,
        )
        return None


class RotatingUserAgentMiddleware:
    """
    Downloader middleware to rotate user agents.

    Helps avoid detection and blocking by rotating through
    different browser user agent strings.
    """

    def __init__(self, user_agents: list[str]):
        self.user_agents = user_agents

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        """Create middleware from crawler settings."""
        user_agents = crawler.settings.getlist("USER_AGENTS", USER_AGENTS)
        return cls(user_agents)

    def process_request(
        self,
        request: Request,
        spider: Spider,
    ) -> Optional[Request]:
        """Add random user agent to request."""
        if self.user_agents:
            request.headers["User-Agent"] = random.choice(self.user_agents)
        return None


class RequestLoggingMiddleware:
    """
    Downloader middleware for request/response logging.

    Logs all requests and responses for debugging and monitoring.
    """

    def process_request(
        self,
        request: Request,
        spider: Spider,
    ) -> Optional[Request]:
        """Log outgoing request."""
        logger.debug(
            f"Request: {request.method} {request.url}",
            extra={
                "method": request.method,
                "url": request.url,
                "depth": request.meta.get("depth", 0),
                "tenant_id": getattr(spider, "tenant_id", None),
            },
        )
        return None

    def process_response(
        self,
        request: Request,
        response: Response,
        spider: Spider,
    ) -> Response:
        """Log received response."""
        logger.debug(
            f"Response: {response.status} {response.url} "
            f"({len(response.body)} bytes)",
            extra={
                "status": response.status,
                "url": response.url,
                "size": len(response.body),
                "tenant_id": getattr(spider, "tenant_id", None),
            },
        )
        return response

    def process_exception(
        self,
        request: Request,
        exception: Exception,
        spider: Spider,
    ) -> Optional[Response]:
        """Log request exceptions."""
        logger.error(
            f"Request failed: {request.url} - {exception}",
            extra={
                "url": request.url,
                "exception": str(exception),
                "tenant_id": getattr(spider, "tenant_id", None),
            },
        )
        return None
