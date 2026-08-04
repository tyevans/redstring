"""
Configuration management for the Knowledge Mapper backend.

Uses pydantic-settings to load configuration from environment variables
with sensible defaults for development.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    APP_NAME: str = "Knowledge Mapper"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True

    # API
    API_V1_PREFIX: str = "/api/v1"

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:5173"]
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: list[str] = ["*"]
    CORS_ALLOW_HEADERS: list[str] = ["*"]

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    RELOAD: bool = True

    # Redis Configuration
    REDIS_URL: str = "redis://default:knowledge_mapper_redis_pass@redis:6379/0"

    # Rate Limiting Configuration
    RATE_LIMIT_ENABLED: bool = True  # Enable/disable rate limiting
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 100  # General auth request limit
    RATE_LIMIT_FAILED_AUTH_PER_MINUTE: int = 10  # Failed auth attempt limit
    RATE_LIMIT_WINDOW_SECONDS: int = 60  # Time window for rate limiting

    # ==========================================================================
    # Event Sourcing Configuration
    # Integration with eventsource-py library for event-driven architecture
    # ==========================================================================

    # Master toggles
    EVENT_STORE_ENABLED: bool = True  # Enable/disable event sourcing
    EVENT_STORE_OUTBOX_ENABLED: bool = True  # Use transactional outbox pattern

    # Snapshot configuration
    SNAPSHOT_ENABLED: bool = True  # Enable aggregate snapshots
    SNAPSHOT_THRESHOLD: int = 100  # Events between automatic snapshots

    # ==========================================================================
    # Kafka Configuration
    # Event bus for distributed event streaming
    # ==========================================================================

    KAFKA_ENABLED: bool = True  # Enable/disable Kafka integration
    KAFKA_BOOTSTRAP_SERVERS: str = "kafka:9092"  # Kafka broker addresses
    KAFKA_TOPIC_PREFIX: str = "events"  # Prefix for all event topics
    KAFKA_CONSUMER_GROUP: str = "knowledge-mapper"  # Consumer group ID

    # Producer settings
    KAFKA_ACKS: str = "all"  # Acknowledgment level: "0", "1", or "all"
    KAFKA_COMPRESSION_TYPE: str = "gzip"  # Compression: "none", "gzip", "snappy", "lz4"
    KAFKA_BATCH_SIZE: int = 16384  # Batch size in bytes
    KAFKA_LINGER_MS: int = 10  # Wait time for batching (milliseconds)

    # Consumer settings
    KAFKA_AUTO_OFFSET_RESET: str = "earliest"  # Start position: "earliest" or "latest"
    KAFKA_SESSION_TIMEOUT_MS: int = 30000  # Session timeout (milliseconds)
    KAFKA_HEARTBEAT_INTERVAL_MS: int = 10000  # Heartbeat interval (milliseconds)

    # ==========================================================================
    # Security Headers Configuration (P2-02)
    # These settings control the SecurityHeadersMiddleware behavior
    # Reference: OWASP Secure Headers Project
    # ==========================================================================

    # Master toggle for security headers
    SECURITY_HEADERS_ENABLED: bool = True

    # Content-Security-Policy (CSP)  # noqa: ERA001 -- prose, not commented-out code
    # Default allows Lit components (requires unsafe-inline for script/style)
    CSP_ENABLED: bool = True
    CSP_DEFAULT_SRC: str = "'self'"
    CSP_SCRIPT_SRC: str = "'self' 'unsafe-inline'"
    CSP_STYLE_SRC: str = "'self' 'unsafe-inline'"
    CSP_IMG_SRC: str = "'self' data: https:"
    CSP_FONT_SRC: str = "'self'"
    CSP_CONNECT_SRC: str = "'self'"  # Will be extended with FRONTEND_URL in main.py
    CSP_FRAME_ANCESTORS: str = "'none'"
    CSP_BASE_URI: str = "'self'"
    CSP_FORM_ACTION: str = "'self'"
    CSP_REPORT_URI: str = ""  # Empty = disabled, set to CSP reporting endpoint

    # Strict-Transport-Security (HSTS)  # noqa: ERA001 -- prose, not commented-out code
    # Only applied for HTTPS requests
    HSTS_ENABLED: bool = True
    HSTS_MAX_AGE: int = 31536000  # 1 year in seconds
    HSTS_INCLUDE_SUBDOMAINS: bool = True
    HSTS_PRELOAD: bool = False  # Requires careful consideration before enabling

    # Other Security Headers
    X_FRAME_OPTIONS: str = "DENY"  # DENY, SAMEORIGIN, or empty to disable
    X_CONTENT_TYPE_OPTIONS: str = "nosniff"
    REFERRER_POLICY: str = "strict-origin-when-cross-origin"
    PERMISSIONS_POLICY: str = (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), "
        "microphone=(), payment=(), usb=()"
    )
    X_XSS_PROTECTION: str = "1; mode=block"  # Legacy but still useful

    # ==========================================================================
    # Neo4j Configuration
    # Knowledge graph database for storing entities and relationships
    # ==========================================================================

    NEO4J_URI: str = "bolt://neo4j:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "knowledge_mapper_neo4j_pass"
    NEO4J_DATABASE: str = "neo4j"  # Default database (Community only supports one)
    NEO4J_MAX_CONNECTION_POOL_SIZE: int = 50
    NEO4J_CONNECTION_TIMEOUT: int = 30

    # ==========================================================================
    # Celery Configuration
    # Distributed task queue for web scraping and entity extraction
    # ==========================================================================

    CELERY_BROKER_URL: str = "redis://default:knowledge_mapper_redis_pass@redis:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://default:knowledge_mapper_redis_pass@redis:6379/2"
    CELERY_WORKER_CONCURRENCY: int = 4
    CELERY_TASK_SOFT_TIME_LIMIT: int = 3600  # 1 hour soft limit
    CELERY_TASK_TIME_LIMIT: int = 3900  # 1 hour 5 min hard limit
    CELERY_TASK_ACKS_LATE: bool = True  # Requeue on worker failure
    CELERY_TASK_REJECT_ON_WORKER_LOST: bool = True

    # ==========================================================================
    # LLM Configuration (Anthropic Claude)
    # For semantic entity extraction from scraped content
    # ==========================================================================

    ANTHROPIC_API_KEY: str = ""  # Required for LLM extraction
    LLM_MODEL: str = "claude-sonnet-4-20250514"
    LLM_MAX_TOKENS: int = 4096
    LLM_RATE_LIMIT_RPM: int = 50  # Requests per minute per tenant
    LLM_DAILY_COST_LIMIT: float = 10.0  # USD per tenant per day
    LLM_FALLBACK_ENABLED: bool = True  # Use spaCy fallback if LLM unavailable

    # ==========================================================================
    # Ollama Configuration (Local LLM)
    # For local entity extraction using Ollama-hosted models
    # Provides cost-effective, privacy-preserving extraction alternative
    # ==========================================================================

    OLLAMA_ENABLED: bool = True  # Enable/disable Ollama extraction
    OLLAMA_BASE_URL: str = "http://192.168.1.14:11434"  # Ollama server URL
    OLLAMA_MODEL: str = "gpt-oss:20b"  # Model for entity extraction
    OLLAMA_TIMEOUT: int = 300  # Request timeout in seconds (5 min for large models)
    OLLAMA_MAX_RETRIES: int = 3  # Max retry attempts on failure
    OLLAMA_RATE_LIMIT_RPM: int = 30  # Requests per minute per tenant
    OLLAMA_MAX_CONTEXT_LENGTH: int = 64000  # Max content characters to send
    OLLAMA_TEMPERATURE: float = 0.1  # Low temperature for deterministic extraction

    # ==========================================================================
    # Embedding Configuration (Ollama with bge-m3)
    # Used for semantic similarity in entity consolidation
    # ==========================================================================

    OLLAMA_EMBEDDING_MODEL: str = "bge-m3:latest"  # Embedding model name
    OLLAMA_EMBEDDING_TIMEOUT: float = 30.0  # Request timeout in seconds
    EMBEDDING_DIMENSION: int = 1024  # bge-m3 produces 1024-dimensional vectors
    EMBEDDING_BATCH_SIZE: int = 32  # Batch size for embedding computation
    EMBEDDING_CACHE_TTL: int = 604800  # Cache TTL in seconds (7 days)

    # ==========================================================================
    # Text Preprocessing Configuration
    # Chunking and entity merging before LLM extraction
    # Modular architecture with swappable chunkers and mergers
    # ==========================================================================

    # Master toggles
    CHUNKING_ENABLED: bool = True  # Enable document chunking

    # Chunker settings
    # Options: "sliding_window" (default)  # noqa: ERA001 -- prose, not commented-out code
    CHUNKER_TYPE: str = "sliding_window"
    CHUNK_SIZE: int = 8000  # Maximum characters per chunk
    CHUNK_OVERLAP: int = 200  # Characters of overlap between chunks
    MAX_CHUNKS_PER_DOCUMENT: int = 200  # Safety limit

    # Entity merger settings
    # Options: "simple", "llm" (default)
    ENTITY_MERGING_ENABLED: bool = True  # Enable cross-chunk entity merging
    MERGER_TYPE: str = "llm"  # Use LLM for ambiguous resolution
    MERGER_HIGH_SIMILARITY_THRESHOLD: float = 0.90  # Auto-merge threshold
    MERGER_LOW_SIMILARITY_THRESHOLD: float = 0.70  # Min for LLM consideration
    MERGER_USE_LLM: bool = True  # Use LLM for ambiguous cases
    MERGER_LLM_BATCH_SIZE: int = 10  # Candidates per LLM call

    # ==========================================================================
    # Encryption Configuration
    # Field-level encryption for sensitive data (API keys, secrets)
    # Uses Fernet (AES-128-CBC + HMAC-SHA256) with per-tenant key derivation
    # ==========================================================================

    ENCRYPTION_ENABLED: bool = True  # Enable/disable field encryption
    # Master encryption key (Fernet format - 32-byte URL-safe base64)
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # noqa: E501
    # CRITICAL: Change this in production and keep it secret!
    ENCRYPTION_MASTER_KEY: str = ""  # Required when ENCRYPTION_ENABLED=True

    # ==========================================================================
    # Web Scraping Configuration
    # Default settings for Scrapy spiders (overridable per-job)
    # ==========================================================================

    SCRAPE_DEFAULT_DEPTH: int = 3
    SCRAPE_DEFAULT_DELAY: float = 1.0  # Seconds between requests
    SCRAPE_MAX_PAGES_PER_JOB: int = 1000
    SCRAPE_CONCURRENT_REQUESTS: int = 8
    SCRAPE_USER_AGENT: str = "KnowledgeMapper/1.0 (+https://github.com/knowledge-mapper)"
    SCRAPE_RESPECT_ROBOTS: bool = True

    # ==========================================================================
    # Inference Testing Configuration
    # Interactive LLM inference testing playground
    # ==========================================================================

    # General inference settings
    INFERENCE_ENABLED: bool = True  # Enable/disable inference testing feature
    INFERENCE_DEFAULT_TIMEOUT: int = 60  # Request timeout in seconds
    INFERENCE_MAX_PROMPT_LENGTH: int = 100000  # Max prompt characters
    INFERENCE_MAX_RESPONSE_TOKENS: int = 100000  # Max response tokens

    # Rate limiting configuration
    # Format: "requests_per_minute" - can be overridden per tenant/provider
    INFERENCE_RATE_LIMIT_RPM: int = 30  # Global default
    INFERENCE_RATE_LIMIT_BURST: int = 5  # Allow burst above limit

    # Rate limit presets (referenced by name in provider configs)
    # Conservative: Lower limits for expensive/slow providers
    # Balanced: Good for most use cases
    # Permissive: For local providers with no cost concerns
    INFERENCE_RATE_LIMIT_PRESETS: dict[str, dict[str, int]] = {
        "conservative": {"rpm": 10, "burst": 2},
        "balanced": {"rpm": 30, "burst": 5},
        "permissive": {"rpm": 100, "burst": 20},
    }

    # Default parameters for inference requests
    INFERENCE_DEFAULT_TEMPERATURE: float = 0.7
    INFERENCE_DEFAULT_MAX_TOKENS: int = 1024

    # Streaming configuration
    INFERENCE_STREAMING_ENABLED: bool = True
    INFERENCE_STREAMING_CHUNK_SIZE: int = 100  # characters

    # History configuration
    INFERENCE_HISTORY_RETENTION_DAYS: int = 90
    INFERENCE_HISTORY_MAX_RESPONSE_STORED: int = 50000  # truncate larger responses

    # Provider-specific defaults (used when creating providers without explicit config)
    INFERENCE_OLLAMA_DEFAULT_URL: str = "http://192.168.1.14:11434"
    INFERENCE_OLLAMA_DEFAULT_MODEL: str = "gemma3:12b"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: object) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        if isinstance(v, list):
            return v
        return []


# Global settings instance
settings = Settings()
