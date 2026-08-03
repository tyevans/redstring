"""
Preprocessing pipeline orchestration.

Coordinates preprocessors, chunkers, and entity mergers into
a complete document processing pipeline.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from kg_builder.preprocessing.base import Chunker, EntityMerger, Preprocessor
from kg_builder.preprocessing.exceptions import PipelineConfigError
from kg_builder.preprocessing.factory import (
    ChunkerFactory,
    ChunkerType,
    EntityMergerFactory,
    EntityMergerType,
    PreprocessorFactory,
    PreprocessorType,
)
from kg_builder.preprocessing.schemas import PipelineMetrics

logger = logging.getLogger(__name__)


class Extractor(Protocol):
    """Protocol for extraction services.

    Defines the interface expected by the pipeline for LLM extraction.
    """

    async def extract(
        self,
        content: str,
        page_url: str,
        tenant_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        """Extract entities from content."""
        ...


@dataclass
class PipelineConfig:
    """Configuration for the preprocessing pipeline.

    All settings have sensible defaults and can be overridden
    via constructor or settings.

    Attributes:
        preprocessor_type: Type of preprocessor to use
        preprocessor_config: Additional config for preprocessor
        chunker_type: Type of chunker to use
        chunk_size: Maximum characters per chunk
        chunk_overlap: Characters of overlap between chunks
        chunker_config: Additional config for chunker
        merger_type: Type of entity merger to use
        use_llm_merging: Whether to use LLM for ambiguous merges
        merger_config: Additional config for merger
        skip_preprocessing: Skip the preprocessing step
        skip_chunking: Skip the chunking step (use full content)
        max_chunks: Safety limit on number of chunks
    """

    # Preprocessor settings
    preprocessor_type: PreprocessorType = PreprocessorType.TRAFILATURA
    preprocessor_config: dict[str, Any] = field(default_factory=dict)

    # Chunker settings
    chunker_type: ChunkerType = ChunkerType.SLIDING_WINDOW
    chunk_size: int = 3000
    chunk_overlap: int = 200
    chunker_config: dict[str, Any] = field(default_factory=dict)

    # Entity merger settings
    merger_type: EntityMergerType = EntityMergerType.LLM
    use_llm_merging: bool = True
    merger_config: dict[str, Any] = field(default_factory=dict)

    # Pipeline behavior
    skip_preprocessing: bool = False
    skip_chunking: bool = False
    max_chunks: int = 20  # Safety limit

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if self.chunk_size <= 0:
            raise PipelineConfigError(f"chunk_size must be > 0, got {self.chunk_size}")
        if self.chunk_overlap < 0:
            raise PipelineConfigError(
                f"chunk_overlap must be >= 0, got {self.chunk_overlap}"
            )
        if self.chunk_overlap >= self.chunk_size:
            raise PipelineConfigError(
                f"chunk_overlap ({self.chunk_overlap}) must be < chunk_size ({self.chunk_size})"
            )
        if self.max_chunks <= 0:
            raise PipelineConfigError(f"max_chunks must be > 0, got {self.max_chunks}")


@dataclass
class PipelineResult:
    """Result from running the preprocessing pipeline.

    Contains extracted entities and relationships along with
    processing metrics.
    """

    entities: list[dict]
    relationships: list[dict]

    # Metrics
    metrics: PipelineMetrics = field(default_factory=PipelineMetrics)

    # Content info
    original_length: int = 0
    preprocessed_length: int = 0
    num_chunks: int = 0

    # Method info
    preprocessing_method: str = ""
    chunking_method: str = ""
    merging_method: str = ""

    # Per-chunk details
    entities_per_chunk: list[int] = field(default_factory=list)
    chunk_errors: list[str] = field(default_factory=list)


class PreprocessingPipeline:
    """Orchestrates preprocessing, chunking, and entity merging.

    The pipeline processes documents through four stages:
    1. Preprocess: Clean HTML, remove boilerplate
    2. Chunk: Split into smaller pieces
    3. Extract: Run LLM extraction on each chunk
    4. Merge: Combine entities across chunks

    Example:
        config = PipelineConfig(chunk_size=3000, chunk_overlap=200)
        pipeline = PreprocessingPipeline(config)

        result = await pipeline.process(
            content=html_content,
            extractor=ollama_extraction_service,
            url="https://example.com/article",
        )

        print(f"Extracted {len(result.entities)} entities from {result.num_chunks} chunks")
    """

    def __init__(self, config: PipelineConfig | None = None):
        """Initialize the preprocessing pipeline.

        Args:
            config: Pipeline configuration (uses defaults if None)
        """
        self._config = config or PipelineConfig()

        # Create components (lazy - only create when needed)
        self._preprocessor: Preprocessor | None = None
        self._chunker: Chunker | None = None
        self._merger: EntityMerger | None = None

        logger.info(
            "PreprocessingPipeline initialized",
            extra={
                "preprocessor_type": self._config.preprocessor_type.value,
                "chunker_type": self._config.chunker_type.value,
                "merger_type": self._config.merger_type.value,
                "chunk_size": self._config.chunk_size,
                "chunk_overlap": self._config.chunk_overlap,
            },
        )

    @property
    def preprocessor(self) -> Preprocessor:
        """Get or create preprocessor instance."""
        if self._preprocessor is None:
            self._preprocessor = PreprocessorFactory.create(
                self._config.preprocessor_type,
                self._config.preprocessor_config,
            )
        return self._preprocessor

    @property
    def chunker(self) -> Chunker:
        """Get or create chunker instance."""
        if self._chunker is None:
            config = self._config.chunker_config.copy()
            config.setdefault("default_chunk_size", self._config.chunk_size)
            config.setdefault("default_overlap", self._config.chunk_overlap)
            self._chunker = ChunkerFactory.create(self._config.chunker_type, config)
        return self._chunker

    @property
    def merger(self) -> EntityMerger:
        """Get or create entity merger instance."""
        if self._merger is None:
            config = self._config.merger_config.copy()
            config.setdefault("use_llm_for_ambiguous", self._config.use_llm_merging)
            self._merger = EntityMergerFactory.create(self._config.merger_type, config)
        return self._merger

    async def process(
        self,
        content: str,
        extractor: Extractor,
        content_type: str = "text/html",
        url: str | None = None,
        tenant_id: UUID | None = None,
    ) -> PipelineResult:
        """Run the complete preprocessing pipeline.

        Args:
            content: Raw HTML or text content
            extractor: Extraction service with async extract() method
            content_type: MIME type of content
            url: Source URL
            tenant_id: Tenant ID for rate limiting

        Returns:
            PipelineResult with merged entities and relationships

        Raises:
            PipelineError: If processing fails unrecoverably
        """
        metrics = PipelineMetrics()
        original_length = len(content)
        total_start = time.time()

        logger.info(
            "Starting preprocessing pipeline",
            extra={
                "content_length": original_length,
                "content_type": content_type,
                "url": url,
            },
        )

        # =================================================================
        # Step 1: Preprocess
        # =================================================================
        preprocess_start = time.time()

        if not self._config.skip_preprocessing:
            try:
                preprocess_result = self.preprocessor.preprocess(
                    content=content,
                    content_type=content_type,
                    url=url,
                )
                clean_text = preprocess_result.clean_text
                preprocessing_method = preprocess_result.preprocessing_method
            except Exception as e:
                logger.error(f"Preprocessing failed: {e}, using raw content")
                clean_text = content
                preprocessing_method = "failed"
        else:
            clean_text = content
            preprocessing_method = "skipped"

        preprocessed_length = len(clean_text)
        metrics.preprocessing_time_ms = (time.time() - preprocess_start) * 1000

        logger.debug(
            "Preprocessing complete",
            extra={
                "original_length": original_length,
                "preprocessed_length": preprocessed_length,
                "method": preprocessing_method,
            },
        )

        # =================================================================
        # Step 2: Chunk
        # =================================================================
        chunk_start = time.time()

        if not self._config.skip_chunking and preprocessed_length > self._config.chunk_size:
            try:
                chunking_result = self.chunker.chunk(
                    text=clean_text,
                    max_chunk_size=self._config.chunk_size,
                    overlap_size=self._config.chunk_overlap,
                )
                chunks = chunking_result.chunks[: self._config.max_chunks]
                chunking_method = chunking_result.chunking_method
            except Exception as e:
                logger.error(f"Chunking failed: {e}, using single chunk")
                from kg_builder.preprocessing.schemas import Chunk

                chunks = [
                    Chunk(
                        text=clean_text,
                        chunk_index=0,
                        start_char=0,
                        end_char=len(clean_text),
                    )
                ]
                chunking_method = "failed"
        else:
            from kg_builder.preprocessing.schemas import Chunk

            chunks = [
                Chunk(
                    text=clean_text,
                    chunk_index=0,
                    start_char=0,
                    end_char=len(clean_text),
                )
            ]
            chunking_method = "skipped" if self._config.skip_chunking else "single_chunk"

        num_chunks = len(chunks)
        metrics.chunking_time_ms = (time.time() - chunk_start) * 1000

        logger.info(
            "Chunking complete",
            extra={
                "num_chunks": num_chunks,
                "method": chunking_method,
                "chunk_sizes": [c.length for c in chunks],
            },
        )

        # =================================================================
        # Step 3: Extract from each chunk
        # =================================================================
        extract_start = time.time()

        entities_by_chunk: dict[int, list[dict]] = {}
        relationships_by_chunk: dict[int, list[dict]] = {}
        entities_per_chunk: list[int] = []
        chunk_errors: list[str] = []

        for chunk in chunks:
            try:
                result = await extractor.extract(
                    content=chunk.text,
                    page_url=url or "",
                    tenant_id=tenant_id,
                )

                # Convert extraction result to dicts
                chunk_entities = []
                chunk_relationships = []

                # Handle different result types
                if hasattr(result, "entities"):
                    for entity in result.entities:
                        entity_dict = {
                            "name": getattr(entity, "name", ""),
                            "type": getattr(entity, "entity_type", ""),
                            "description": getattr(entity, "description", None),
                            "confidence": getattr(entity, "confidence", 1.0),
                            "properties": getattr(entity, "properties", {}) or {},
                            "source_text": getattr(entity, "source_text", None),
                            "_chunk_index": chunk.chunk_index,
                        }
                        # Handle enum types
                        if hasattr(entity_dict["type"], "value"):
                            entity_dict["type"] = entity_dict["type"].value
                        chunk_entities.append(entity_dict)

                if hasattr(result, "relationships"):
                    for rel in result.relationships:
                        rel_dict = {
                            "source_name": getattr(rel, "source_name", ""),
                            "target_name": getattr(rel, "target_name", ""),
                            "relationship_type": getattr(rel, "relationship_type", ""),
                            "confidence": getattr(rel, "confidence", 1.0),
                            "context": getattr(rel, "context", None),
                            "properties": getattr(rel, "properties", {}) or {},
                            "_chunk_index": chunk.chunk_index,
                        }
                        # Handle enum types
                        if hasattr(rel_dict["relationship_type"], "value"):
                            rel_dict["relationship_type"] = rel_dict["relationship_type"].value
                        chunk_relationships.append(rel_dict)

                entities_by_chunk[chunk.chunk_index] = chunk_entities
                relationships_by_chunk[chunk.chunk_index] = chunk_relationships
                entities_per_chunk.append(len(chunk_entities))
                metrics.chunks_processed += 1

            except Exception as e:
                logger.error(
                    f"Extraction failed for chunk {chunk.chunk_index}: {e}",
                    extra={"url": url, "chunk_index": chunk.chunk_index},
                )
                entities_by_chunk[chunk.chunk_index] = []
                relationships_by_chunk[chunk.chunk_index] = []
                entities_per_chunk.append(0)
                chunk_errors.append(f"Chunk {chunk.chunk_index}: {e!s}")
                metrics.chunks_failed += 1

        metrics.extraction_time_ms = (time.time() - extract_start) * 1000
        metrics.entities_before_merge = sum(
            len(e) for e in entities_by_chunk.values()
        )
        metrics.relationships_before_merge = sum(
            len(r) for r in relationships_by_chunk.values()
        )

        logger.info(
            "Extraction complete",
            extra={
                "chunks_processed": metrics.chunks_processed,
                "chunks_failed": metrics.chunks_failed,
                "entities_extracted": metrics.entities_before_merge,
                "relationships_extracted": metrics.relationships_before_merge,
            },
        )

        # =================================================================
        # Step 4: Merge entities across chunks
        # =================================================================
        merge_start = time.time()

        try:
            merged_entities, merged_relationships = await self.merger.merge_entities(
                entities_by_chunk=entities_by_chunk,
                relationships_by_chunk=relationships_by_chunk,
                document_context=clean_text[:2000] if len(clean_text) > 2000 else clean_text,
            )
            merging_method = self.merger.merger_type
        except Exception as e:
            logger.error(f"Merging failed: {e}, returning unmerged entities")
            merged_entities = [
                entity
                for entities in entities_by_chunk.values()
                for entity in entities
            ]
            merged_relationships = [
                rel
                for rels in relationships_by_chunk.values()
                for rel in rels
            ]
            merging_method = "failed"

        # Clean up internal keys from entities
        for entity in merged_entities:
            entity.pop("_chunk_index", None)
            entity.pop("_merged_from", None)

        for rel in merged_relationships:
            rel.pop("_chunk_index", None)

        metrics.merging_time_ms = (time.time() - merge_start) * 1000
        metrics.entities_after_merge = len(merged_entities)
        metrics.relationships_after_merge = len(merged_relationships)
        metrics.total_time_ms = (time.time() - total_start) * 1000

        logger.info(
            "Pipeline complete",
            extra={
                "entities_final": len(merged_entities),
                "relationships_final": len(merged_relationships),
                "total_time_ms": round(metrics.total_time_ms, 2),
                "url": url,
            },
        )

        return PipelineResult(
            entities=merged_entities,
            relationships=merged_relationships,
            metrics=metrics,
            original_length=original_length,
            preprocessed_length=preprocessed_length,
            num_chunks=num_chunks,
            preprocessing_method=preprocessing_method,
            chunking_method=chunking_method,
            merging_method=merging_method,
            entities_per_chunk=entities_per_chunk,
            chunk_errors=chunk_errors,
        )
