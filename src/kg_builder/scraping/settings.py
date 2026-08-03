"""
Scrapy settings for the Knowledge Mapper spider.

These settings are configured for tenant-aware scraping with:
- Rate limiting
- robots.txt respect
- Content extraction
- Custom pipelines
"""

from kg_builder.config import settings as app_settings

# Scrapy settings
BOT_NAME = "knowledge_mapper"
SPIDER_MODULES = ["kg_builder.scraping.spiders"]
NEWSPIDER_MODULE = "kg_builder.scraping.spiders"

# Crawl responsibly by identifying yourself
USER_AGENT = (
    f"KnowledgeMapper/{app_settings.APP_VERSION} "
    f"(+{app_settings.FRONTEND_URL or 'https://knowledge-mapper.example.com'})"
)

# Obey robots.txt rules (can be overridden per job)
ROBOTSTXT_OBEY = True

# Configure maximum concurrent requests
CONCURRENT_REQUESTS = 8
CONCURRENT_REQUESTS_PER_DOMAIN = 4

# Configure a delay for requests for the same website
DOWNLOAD_DELAY = 1.0
RANDOMIZE_DOWNLOAD_DELAY = True

# Disable cookies (enabled per spider if needed)
COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default in dev)
TELNETCONSOLE_ENABLED = False

# Override the default request headers
DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Enable or disable spider middlewares
SPIDER_MIDDLEWARES = {
    "kg_builder.scraping.middlewares.TenantContextMiddleware": 543,
}

# Enable or disable downloader middlewares
DOWNLOADER_MIDDLEWARES = {
    "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
    "kg_builder.scraping.middlewares.RotatingUserAgentMiddleware": 400,
    "kg_builder.scraping.middlewares.RequestLoggingMiddleware": 450,
}

# Enable or disable extensions
EXTENSIONS = {
    "scrapy.extensions.corestats.CoreStats": 0,
    "scrapy.extensions.memusage.MemoryUsage": 0,
    "scrapy.extensions.logstats.LogStats": 0,
}

# Configure item pipelines
ITEM_PIPELINES = {
    "kg_builder.scraping.pipelines.ContentExtractionPipeline": 100,
    "kg_builder.scraping.pipelines.SchemaOrgExtractionPipeline": 200,
    "kg_builder.scraping.pipelines.DatabaseStoragePipeline": 300,
    "kg_builder.scraping.pipelines.ExtractionQueuePipeline": 400,
}

# Enable and configure the AutoThrottle extension
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1
AUTOTHROTTLE_MAX_DELAY = 10
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0
AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching (disabled by default)
HTTPCACHE_ENABLED = False
HTTPCACHE_EXPIRATION_SECS = 0
HTTPCACHE_DIR = "httpcache"
HTTPCACHE_IGNORE_HTTP_CODES = [500, 502, 503, 504]
HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"

# Log settings
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

# Request fingerprinting
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"

# Async/await
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

# Memory management
MEMUSAGE_ENABLED = True
MEMUSAGE_LIMIT_MB = 512
MEMUSAGE_WARNING_MB = 256

# Depth limits
DEPTH_LIMIT = 10
DEPTH_PRIORITY = 1
DEPTH_STATS_VERBOSE = True

# Retry settings
RETRY_ENABLED = True
RETRY_TIMES = 2
RETRY_HTTP_CODES = [500, 502, 503, 504, 408, 429]

# Redirect settings
REDIRECT_ENABLED = True
REDIRECT_MAX_TIMES = 5

# Download timeout
DOWNLOAD_TIMEOUT = 30

# DNS timeout
DNS_TIMEOUT = 10

# Response size limits (5MB max)
DOWNLOAD_MAXSIZE = 5 * 1024 * 1024
DOWNLOAD_WARNSIZE = 1 * 1024 * 1024


def get_scrapy_settings(job) -> dict:
    """
    Get Scrapy settings customized for a specific job.

    Args:
        job: ScrapingJob instance

    Returns:
        dict: Scrapy settings dictionary
    """
    settings_dict = {
        "BOT_NAME": BOT_NAME,
        "SPIDER_MODULES": SPIDER_MODULES,
        "USER_AGENT": USER_AGENT,
        "ROBOTSTXT_OBEY": job.respect_robots_txt,
        "CONCURRENT_REQUESTS_PER_DOMAIN": max(1, int(job.crawl_speed * 2)),
        "DOWNLOAD_DELAY": 1.0 / job.crawl_speed if job.crawl_speed > 0 else 1.0,
        "DEPTH_LIMIT": job.crawl_depth,
        "CLOSESPIDER_PAGECOUNT": job.max_pages,
        "SPIDER_MIDDLEWARES": SPIDER_MIDDLEWARES,
        "DOWNLOADER_MIDDLEWARES": DOWNLOADER_MIDDLEWARES,
        "ITEM_PIPELINES": ITEM_PIPELINES,
        "AUTOTHROTTLE_ENABLED": AUTOTHROTTLE_ENABLED,
        "AUTOTHROTTLE_START_DELAY": AUTOTHROTTLE_START_DELAY,
        "AUTOTHROTTLE_MAX_DELAY": AUTOTHROTTLE_MAX_DELAY,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": min(job.crawl_speed, 4.0),
        "LOG_LEVEL": LOG_LEVEL,
        "REQUEST_FINGERPRINTER_IMPLEMENTATION": REQUEST_FINGERPRINTER_IMPLEMENTATION,
        "TWISTED_REACTOR": TWISTED_REACTOR,
        "MEMUSAGE_ENABLED": MEMUSAGE_ENABLED,
        "MEMUSAGE_LIMIT_MB": MEMUSAGE_LIMIT_MB,
        "RETRY_ENABLED": RETRY_ENABLED,
        "RETRY_TIMES": RETRY_TIMES,
        "DOWNLOAD_TIMEOUT": DOWNLOAD_TIMEOUT,
        "DOWNLOAD_MAXSIZE": DOWNLOAD_MAXSIZE,
    }

    # Apply custom settings from job
    if job.custom_settings:
        settings_dict.update(job.custom_settings)

    return settings_dict
