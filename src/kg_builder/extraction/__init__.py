"""
Entity extraction pipeline for Knowledge Mapper.

This package provides:
- Schema.org extraction from JSON-LD and microdata
- LLM-based entity extraction using Claude
- Ollama-based local entity extraction using pydantic-ai
- Entity deduplication and normalization
- Pydantic schemas for structured extraction output
- Optimized extraction prompts for different documentation types
- Retry logic with exponential backoff for resilient extraction
- Circuit breaker pattern for Ollama failure handling
- Extraction worker task for background processing
- Adaptive extraction with domain-specific schemas
- Content classification for automatic domain detection
- Strategy routing for extraction mode selection (legacy, manual, auto_detect)
"""

from kg_builder.extraction.schema_org import (
    extract_entities_from_schema_org,
    extract_entities_from_open_graph,
)
from kg_builder.extraction.llm_extractor import extract_entities_with_llm
from kg_builder.extraction.ollama_extractor import (
    OllamaExtractionService,
    ExtractionError,
    get_ollama_extraction_service,
    reset_ollama_extraction_service,
)
from kg_builder.extraction.rate_limiter import (
    OllamaRateLimiter,
    RateLimitExceeded,
    get_rate_limiter,
    reset_rate_limiter,
)
from kg_builder.extraction.retry import (
    RetryExhausted,
    ExtractionRetryPolicy,
    with_retry,
)
from kg_builder.extraction.circuit_breaker import (
    CircuitState,
    CircuitOpen,
    OllamaCircuitBreaker,
    get_circuit_breaker,
    reset_circuit_breaker,
)
from kg_builder.extraction.prompts import (
    DocumentationType,
    get_system_prompt,
    build_user_prompt,
    get_entity_types,
    get_relationship_types,
    SYSTEM_PROMPT_BASE,
    SYSTEM_PROMPT_API_REFERENCE,
    SYSTEM_PROMPT_TUTORIAL,
    SYSTEM_PROMPT_CONCEPTUAL,
    SYSTEM_PROMPT_EXAMPLE_CODE,
)
from kg_builder.extraction.schemas import (
    # Type literals
    EntityTypeLiteral,
    RelationshipTypeLiteral,
    # Property schemas
    FunctionProperties,
    ClassProperties,
    ModuleProperties,
    PatternProperties,
    ExampleProperties,
    ParameterProperties,
    ExceptionProperties,
    # Main schemas
    ExtractedEntitySchema,
    ExtractedRelationshipSchema,
    ExtractionResult,
    # Utility
    get_property_schema_for_type,
)
from kg_builder.extraction.strategy_router import (
    ExtractionStrategyRouter,
    ExtractionStrategyRouterFactory,
    JobUpdateCallback,
    get_strategy_router_factory,
    route_extraction_strategy,
)
from kg_builder.extraction.classifier import (
    ContentClassifier,
    classify_content,
)
from kg_builder.extraction.prompt_generator import (
    DomainPromptGenerator,
    generate_extraction_prompt,
    generate_output_schema,
    generate_user_prompt,
    get_prompt_generator,
    reset_prompt_generator,
)

__all__ = [
    # Extraction functions
    "extract_entities_from_schema_org",
    "extract_entities_from_open_graph",
    "extract_entities_with_llm",
    # Ollama extraction service
    "OllamaExtractionService",
    "ExtractionError",
    "get_ollama_extraction_service",
    "reset_ollama_extraction_service",
    # Rate limiting
    "OllamaRateLimiter",
    "RateLimitExceeded",
    "get_rate_limiter",
    "reset_rate_limiter",
    # Retry logic
    "RetryExhausted",
    "ExtractionRetryPolicy",
    "with_retry",
    # Circuit breaker
    "CircuitState",
    "CircuitOpen",
    "OllamaCircuitBreaker",
    "get_circuit_breaker",
    "reset_circuit_breaker",
    # Prompt utilities
    "DocumentationType",
    "get_system_prompt",
    "build_user_prompt",
    "get_entity_types",
    "get_relationship_types",
    "SYSTEM_PROMPT_BASE",
    "SYSTEM_PROMPT_API_REFERENCE",
    "SYSTEM_PROMPT_TUTORIAL",
    "SYSTEM_PROMPT_CONCEPTUAL",
    "SYSTEM_PROMPT_EXAMPLE_CODE",
    # Type literals
    "EntityTypeLiteral",
    "RelationshipTypeLiteral",
    # Property schemas
    "FunctionProperties",
    "ClassProperties",
    "ModuleProperties",
    "PatternProperties",
    "ExampleProperties",
    "ParameterProperties",
    "ExceptionProperties",
    # Main schemas
    "ExtractedEntitySchema",
    "ExtractedRelationshipSchema",
    "ExtractionResult",
    # Utility
    "get_property_schema_for_type",
    # Worker
    # Strategy routing
    "ExtractionStrategyRouter",
    "ExtractionStrategyRouterFactory",
    "JobUpdateCallback",
    "get_strategy_router_factory",
    "route_extraction_strategy",
    # Content classification
    "ContentClassifier",
    "classify_content",
    # Prompt generation
    "DomainPromptGenerator",
    "generate_extraction_prompt",
    "generate_output_schema",
    "generate_user_prompt",
    "get_prompt_generator",
    "reset_prompt_generator",
]
