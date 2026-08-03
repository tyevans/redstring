"""
Extraction orchestrator service.

This service coordinates entity and relationship extraction from scraped pages.
It is decoupled from Celery task infrastructure to enable unit testing and
flexible invocation patterns.

Follows the Single Responsibility Principle by focusing solely on extraction
orchestration, delegating persistence and event emission to callers.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from kg_builder.config import settings
from kg_builder.models.extracted_entity import ExtractionMethod

if TYPE_CHECKING:
    from kg_builder.models.extraction_provider import ExtractionProvider, ExtractionProviderType
    from kg_builder.models.scraped_page import ScrapedPage

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    """Result of an extraction operation."""

    entities: list[dict] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    schema_org_count: int = 0
    llm_count: int = 0

    @property
    def total_entities(self) -> int:
        """Total number of entities extracted."""
        return len(self.entities)


class ExtractorProtocol(Protocol):
    """Protocol for extraction services."""

    async def extract(self, content: str, page_url: str = "") -> "ExtractionResult": ...


class ExtractionOrchestrator:
    """
    Orchestrates entity and relationship extraction from scraped pages.

    This class coordinates different extraction strategies:
    - Schema.org/JSON-LD extraction
    - Open Graph extraction
    - LLM-based extraction (Ollama, OpenAI, etc.)

    The orchestrator is stateless and can be used independently of
    Celery task infrastructure, enabling unit testing and flexible
    invocation patterns.

    Example:
        orchestrator = ExtractionOrchestrator()
        result = orchestrator.extract_from_page(
            page=page,
            tenant_id="tenant-123",
            extraction_provider=provider,  # Optional
        )
        # result.entities, result.relationships are ready for persistence
    """

    def extract_from_page(
        self,
        page: "ScrapedPage",
        tenant_id: str,
        extraction_provider: "ExtractionProvider | None" = None,
        use_llm_extraction: bool = True,
    ) -> ExtractionResult:
        """
        Run extraction pipeline on a page.

        Args:
            page: Scraped page with content
            tenant_id: Tenant ID for context
            extraction_provider: Optional specific provider to use
            use_llm_extraction: Whether to run LLM extraction

        Returns:
            ExtractionResult with entities and relationships
        """
        entities: list[dict] = []
        relationships: list[dict] = []
        schema_org_count = 0
        llm_count = 0

        # 1. Extract from Schema.org/JSON-LD
        if page.schema_org_data:
            schema_entities = self._extract_schema_org(page.schema_org_data)
            entities.extend(schema_entities)
            schema_org_count = len(schema_entities)

        # 2. Extract from Open Graph
        if page.open_graph_data:
            og_entities = self._extract_open_graph(page.open_graph_data)
            entities.extend(og_entities)
            schema_org_count += len(og_entities)

        # 3. LLM extraction (if enabled)
        if use_llm_extraction and page.html_content:
            llm_entities, llm_relationships = self._extract_with_llm(
                text=page.html_content,
                tenant_id=tenant_id,
                page_url=page.url,
                extraction_provider=extraction_provider,
            )
            entities.extend(llm_entities)
            relationships.extend(llm_relationships)
            llm_count = len(llm_entities)

        # Deduplicate entities by name and type
        deduplicated = self._deduplicate_entities(entities)

        return ExtractionResult(
            entities=deduplicated,
            relationships=relationships,
            schema_org_count=schema_org_count,
            llm_count=llm_count,
        )

    def _extract_schema_org(self, data: list) -> list[dict]:
        """Extract entities from Schema.org JSON-LD data."""
        from kg_builder.extraction.schema_org import extract_entities_from_schema_org

        return extract_entities_from_schema_org(data)

    def _extract_open_graph(self, data: dict) -> list[dict]:
        """Extract entities from Open Graph data."""
        from kg_builder.extraction.schema_org import extract_entities_from_open_graph

        return extract_entities_from_open_graph(data)

    def _extract_with_llm(
        self,
        text: str,
        tenant_id: str,
        page_url: str = "",
        extraction_provider: "ExtractionProvider | None" = None,
    ) -> tuple[list[dict], list[dict]]:
        """
        Extract entities and relationships using LLM.

        Routes to the appropriate extraction strategy based on configuration:
        - If extraction_provider is specified, uses provider-based extraction
        - If PREPROCESSING_ENABLED, uses the full pipeline
        - Otherwise falls back to legacy extraction

        Args:
            text: HTML content to extract from
            tenant_id: Tenant ID
            page_url: URL of the page
            extraction_provider: Optional specific provider

        Returns:
            Tuple of (entities, relationships)
        """
        # If a provider is specified, use it
        if extraction_provider is not None:
            return self._extract_with_provider(text, tenant_id, page_url, extraction_provider)

        # Check if Ollama is configured for fallback
        if not settings.OLLAMA_BASE_URL:
            logger.warning(
                "OLLAMA_BASE_URL not configured and no provider specified, skipping LLM extraction"
            )
            return [], []

        # Use preprocessing pipeline if enabled
        if settings.PREPROCESSING_ENABLED:
            return self._extract_with_preprocessing_pipeline(text, tenant_id, page_url)
        else:
            return self._extract_with_llm_legacy(text, tenant_id, page_url)

    def _extract_with_provider(
        self,
        text: str,
        tenant_id: str,
        page_url: str,
        extraction_provider: "ExtractionProvider",
    ) -> tuple[list[dict], list[dict]]:
        """
        Extract using a specific configured provider.

        Uses the ExtractionProviderFactory to create the appropriate service
        and run extraction.

        Args:
            text: HTML content to extract from
            tenant_id: Tenant ID
            page_url: URL of the page
            extraction_provider: ExtractionProvider model instance

        Returns:
            Tuple of (entities, relationships)
        """
        from kg_builder.extraction.base import ExtractionError
        from kg_builder.extraction.factory import (
            ExtractionProviderFactory,
            ProviderConfigError,
        )

        logger.info(
            "Starting provider-based extraction",
            extra={
                "page_url": page_url,
                "text_length": len(text),
                "provider_id": str(extraction_provider.id),
                "provider_type": extraction_provider.provider_type.value,
                "provider_name": extraction_provider.name,
            },
        )

        start_time = time.time()

        try:
            # Create service from provider config
            service = ExtractionProviderFactory.create_service(
                extraction_provider,
                UUID(tenant_id),
            )

            # Run async extraction in sync context
            result = asyncio.run(service.extract(content=text, page_url=page_url))

            elapsed = time.time() - start_time

            # Determine extraction method based on provider type
            method = self._get_extraction_method_for_provider(extraction_provider.provider_type)

            # Convert ExtractionResult to list of entity dicts
            entities = self._convert_extraction_result_entities(result, method)
            relationships = self._convert_extraction_result_relationships(result)

            logger.info(
                "Provider-based extraction completed",
                extra={
                    "page_url": page_url,
                    "entities_count": len(entities),
                    "relationships_count": len(relationships),
                    "elapsed_seconds": round(elapsed, 2),
                    "provider_type": extraction_provider.provider_type.value,
                },
            )

            return entities, relationships

        except ProviderConfigError as e:
            elapsed = time.time() - start_time
            logger.error(
                "Provider configuration error",
                extra={
                    "page_url": page_url,
                    "error": str(e),
                    "provider_id": str(extraction_provider.id),
                    "elapsed_seconds": round(elapsed, 2),
                },
            )
            return [], []

        except ExtractionError as e:
            elapsed = time.time() - start_time
            logger.warning(
                f"Provider extraction failed for {page_url} "
                f"(provider={extraction_provider.id}, elapsed={round(elapsed, 2)}s): {e}"
            )
            return [], []

        except Exception as e:
            elapsed = time.time() - start_time
            logger.warning(
                f"Provider extraction failed with unexpected error: {type(e).__name__}: {e}",
                extra={
                    "page_url": page_url,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "provider_id": str(extraction_provider.id),
                    "elapsed_seconds": round(elapsed, 2),
                },
            )
            return [], []

    def _get_extraction_method_for_provider(
        self, provider_type: "ExtractionProviderType"
    ) -> ExtractionMethod:
        """
        Map provider type to extraction method.

        This centralizes the provider-to-method mapping, addressing the
        Open/Closed Principle concern by making it easy to extend with
        new providers.
        """
        from kg_builder.models.extraction_provider import ExtractionProviderType

        method_map = {
            ExtractionProviderType.OPENAI: ExtractionMethod.LLM_OPENAI,
            ExtractionProviderType.ANTHROPIC: ExtractionMethod.LLM_CLAUDE,
            ExtractionProviderType.OLLAMA: ExtractionMethod.LLM_OLLAMA,
        }
        return method_map.get(provider_type, ExtractionMethod.LLM_OLLAMA)

    def _extract_with_preprocessing_pipeline(
        self, text: str, tenant_id: str, page_url: str = ""
    ) -> tuple[list[dict], list[dict]]:
        """
        Extract using the full preprocessing pipeline.

        Pipeline stages:
        1. Preprocess (trafilatura): Clean HTML, remove boilerplate
        2. Chunk (sliding window): Split into overlapping chunks
        3. Extract (Ollama): Run LLM on each chunk
        4. Merge (LLM-assisted): Combine entities across chunks

        Returns:
            Tuple of (entities, relationships)
        """
        from kg_builder.extraction.ollama_extractor import get_ollama_extraction_service
        from kg_builder.preprocessing.factory import (
            ChunkerType,
            EntityMergerType,
            PreprocessorType,
        )
        from kg_builder.preprocessing.pipeline import PipelineConfig, PreprocessingPipeline

        logger.info(
            "Starting preprocessing pipeline extraction",
            extra={
                "page_url": page_url,
                "text_length": len(text),
                "preprocessor": settings.PREPROCESSOR_TYPE,
                "chunker": settings.CHUNKER_TYPE,
                "merger": settings.MERGER_TYPE,
                "chunk_size": settings.CHUNK_SIZE,
            },
        )

        start_time = time.time()

        try:
            # Build pipeline configuration from settings
            config = PipelineConfig(
                preprocessor_type=PreprocessorType(settings.PREPROCESSOR_TYPE),
                preprocessor_config={
                    "favor_recall": settings.PREPROCESSOR_FAVOR_RECALL,
                    "include_tables": settings.PREPROCESSOR_INCLUDE_TABLES,
                },
                chunker_type=ChunkerType(settings.CHUNKER_TYPE),
                chunk_size=settings.CHUNK_SIZE,
                chunk_overlap=settings.CHUNK_OVERLAP,
                merger_type=EntityMergerType(settings.MERGER_TYPE),
                use_llm_merging=settings.MERGER_USE_LLM,
                merger_config={
                    "high_threshold": settings.MERGER_HIGH_SIMILARITY_THRESHOLD,
                    "low_threshold": settings.MERGER_LOW_SIMILARITY_THRESHOLD,
                    "batch_size": settings.MERGER_LLM_BATCH_SIZE,
                },
                skip_preprocessing=not settings.PREPROCESSING_ENABLED,
                skip_chunking=not settings.CHUNKING_ENABLED,
                max_chunks=settings.MAX_CHUNKS_PER_DOCUMENT,
            )

            # Create pipeline and extractor
            pipeline = PreprocessingPipeline(config)
            extractor = get_ollama_extraction_service()

            # Run pipeline (async in sync context)
            result = asyncio.run(
                pipeline.process(
                    content=text,
                    extractor=extractor,
                    content_type="text/html",
                    url=page_url,
                    tenant_id=UUID(tenant_id) if tenant_id else None,
                )
            )

            elapsed = time.time() - start_time

            # Add extraction method to entities
            entities = []
            for entity in result.entities:
                entity_copy = entity.copy()
                entity_copy["method"] = ExtractionMethod.LLM_OLLAMA
                entities.append(entity_copy)

            logger.info(
                "Preprocessing pipeline extraction completed",
                extra={
                    "page_url": page_url,
                    "entities_count": len(entities),
                    "relationships_count": len(result.relationships),
                    "elapsed_seconds": round(elapsed, 2),
                    "original_length": result.original_length,
                    "preprocessed_length": result.preprocessed_length,
                    "num_chunks": result.num_chunks,
                    "preprocessing_method": result.preprocessing_method,
                    "chunking_method": result.chunking_method,
                    "merging_method": result.merging_method,
                    "entities_per_chunk": result.entities_per_chunk,
                },
            )

            return entities, result.relationships

        except Exception as e:
            elapsed = time.time() - start_time
            logger.warning(
                "Preprocessing pipeline extraction failed, falling back to legacy",
                extra={
                    "page_url": page_url,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "elapsed_seconds": round(elapsed, 2),
                },
            )
            # Fall back to legacy extraction on pipeline failure
            return self._extract_with_llm_legacy(text, tenant_id, page_url)

    def _extract_with_llm_legacy(
        self, text: str, tenant_id: str, page_url: str = ""
    ) -> tuple[list[dict], list[dict]]:
        """
        Legacy extraction without preprocessing pipeline.

        Simple truncation-based extraction for backward compatibility.

        Returns:
            Tuple of (entities, relationships)
        """
        from kg_builder.extraction.ollama_extractor import (
            ExtractionError,
            get_ollama_extraction_service,
        )

        logger.info(
            "Starting legacy LLM extraction",
            extra={
                "page_url": page_url,
                "text_length": len(text),
                "ollama_url": settings.OLLAMA_BASE_URL,
                "model": settings.OLLAMA_MODEL,
            },
        )

        start_time = time.time()

        try:
            service = get_ollama_extraction_service()
            # Run async extraction in sync context
            result = asyncio.run(service.extract(content=text, page_url=page_url))

            elapsed = time.time() - start_time

            # Convert ExtractionResult to list of entity dicts
            entities = self._convert_extraction_result_entities(result, ExtractionMethod.LLM_OLLAMA)
            relationships = self._convert_extraction_result_relationships(result)

            logger.info(
                "Legacy LLM extraction completed",
                extra={
                    "page_url": page_url,
                    "entities_count": len(entities),
                    "relationships_count": len(relationships),
                    "elapsed_seconds": round(elapsed, 2),
                    "text_length": len(text),
                },
            )
            return entities, relationships

        except ExtractionError as e:
            elapsed = time.time() - start_time
            logger.warning(
                "Legacy LLM extraction failed with ExtractionError",
                extra={
                    "page_url": page_url,
                    "error": str(e),
                    "cause": str(e.cause) if e.cause else None,
                    "cause_type": type(e.cause).__name__ if e.cause else None,
                    "elapsed_seconds": round(elapsed, 2),
                    "text_length": len(text),
                },
            )
            return [], []

        except Exception as e:
            elapsed = time.time() - start_time
            logger.warning(
                "Legacy LLM extraction failed with unexpected error",
                extra={
                    "page_url": page_url,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "elapsed_seconds": round(elapsed, 2),
                    "text_length": len(text),
                },
            )
            return [], []

    def _convert_extraction_result_entities(self, result, method: ExtractionMethod) -> list[dict]:
        """Convert ExtractionResult entities to dict format."""
        entities = []
        for entity in result.entities:
            # entity_type may be string or enum depending on pydantic-ai parsing
            entity_type = entity.entity_type
            if hasattr(entity_type, "value"):
                entity_type = entity_type.value
            entities.append(
                {
                    "name": entity.name,
                    "type": entity_type,
                    "description": entity.description,
                    "confidence": entity.confidence,
                    "properties": entity.properties or {},
                    "method": method,
                }
            )
        return entities

    def _convert_extraction_result_relationships(self, result) -> list[dict]:
        """Convert ExtractionResult relationships to dict format."""
        relationships = []
        for rel in result.relationships:
            rel_type = rel.relationship_type
            if hasattr(rel_type, "value"):
                rel_type = rel_type.value
            relationships.append(
                {
                    "source_name": rel.source_name,
                    "target_name": rel.target_name,
                    "relationship_type": rel_type,
                    "confidence": rel.confidence,
                    "context": rel.context,
                    "properties": rel.properties or {},
                }
            )
        return relationships

    def _deduplicate_entities(self, entities: list[dict]) -> list[dict]:
        """Deduplicate entities by normalized name and type."""
        seen: set[tuple[str, str]] = set()
        unique = []
        for entity in entities:
            key = (self.normalize_name(entity["name"]), entity["type"])
            if key not in seen:
                seen.add(key)
                unique.append(entity)
        return unique

    @staticmethod
    def normalize_name(name: str) -> str:
        """Normalize entity name for deduplication and matching."""
        return name.lower().strip()
