"""
Abstract base classes (Protocols) for preprocessing components.

This module defines the interfaces for:
- Preprocessors: Clean and extract main content from HTML/text
- Chunkers: Split documents into smaller pieces for processing
- EntityMergers: Combine entities from multiple chunks

Following the Protocol pattern allows for duck typing while maintaining
type safety through runtime_checkable decorators.
"""

from typing import Protocol, runtime_checkable

from kg_builder.preprocessing.schemas import (
    ChunkingResult,
    EntityMergeCandidate,
    EntityMergeDecision,
    PreprocessingResult,
)


@runtime_checkable
class Preprocessor(Protocol):
    """Protocol for text preprocessors.

    Preprocessors extract and clean the main content from raw HTML or text,
    removing boilerplate elements like navigation, headers, footers, ads, etc.

    Implementations must be synchronous as preprocessing is typically CPU-bound.

    Example:
        class MyPreprocessor:
            @property
            def preprocessor_type(self) -> str:
                return "my_preprocessor"

            def preprocess(
                self, content: str, content_type: str, url: str | None
            ) -> PreprocessingResult:
                # Clean content
                return PreprocessingResult(clean_text=cleaned, ...)
    """

    @property
    def preprocessor_type(self) -> str:
        """Return the type identifier for this preprocessor."""
        ...

    def preprocess(
        self,
        content: str,
        content_type: str = "text/html",
        url: str | None = None,
    ) -> PreprocessingResult:
        """Preprocess content to extract clean text.

        Args:
            content: Raw HTML or text content to preprocess
            content_type: MIME type of content (e.g., "text/html", "text/plain")
            url: Optional source URL for context (may help with extraction)

        Returns:
            PreprocessingResult with cleaned text and metadata

        Raises:
            PreprocessorError: If preprocessing fails unrecoverably
        """
        ...


@runtime_checkable
class Chunker(Protocol):
    """Protocol for document chunkers.

    Chunkers split documents into smaller pieces suitable for LLM processing,
    preserving context through overlapping windows where appropriate.

    Implementations must be synchronous as chunking is typically CPU-bound.

    Example:
        class MyChunker:
            @property
            def chunker_type(self) -> str:
                return "my_chunker"

            def chunk(
                self, text: str, max_chunk_size: int | None, overlap_size: int | None
            ) -> ChunkingResult:
                # Split text
                return ChunkingResult(chunks=[...], ...)
    """

    @property
    def chunker_type(self) -> str:
        """Return the type identifier for this chunker.

        This should match the ChunkerType enum value used for registration.
        """
        ...

    def chunk(
        self,
        text: str,
        max_chunk_size: int | None = None,
        overlap_size: int | None = None,
    ) -> ChunkingResult:
        """Split text into chunks.

        Args:
            text: Text to split into chunks
            max_chunk_size: Maximum characters per chunk (uses default if None)
            overlap_size: Characters of overlap between chunks (uses default if None)

        Returns:
            ChunkingResult with list of chunks and metadata

        Raises:
            ChunkerError: If chunking fails unrecoverably
        """
        ...


@runtime_checkable
class EntityMerger(Protocol):
    """Protocol for entity mergers.

    Entity mergers combine entities extracted from multiple chunks,
    resolving duplicates and ambiguous references.

    Implementations must be async as merging may involve LLM calls.

    Example:
        class MyMerger:
            @property
            def merger_type(self) -> str:
                return "my_merger"

            async def merge_entities(
                self, entities_by_chunk, relationships_by_chunk, document_context
            ):
                # Merge entities
                return merged_entities, merged_relationships

            async def resolve_candidates(self, candidates):
                # Resolve ambiguous pairs
                return decisions
    """

    @property
    def merger_type(self) -> str:
        """Return the type identifier for this merger.

        This should match the EntityMergerType enum value used for registration.
        """
        ...

    async def merge_entities(
        self,
        entities_by_chunk: dict[int, list[dict]],
        relationships_by_chunk: dict[int, list[dict]],
        document_context: str | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Merge entities and relationships from multiple chunks.

        Args:
            entities_by_chunk: Map of chunk_index -> list of entity dicts
            relationships_by_chunk: Map of chunk_index -> list of relationship dicts
            document_context: Optional document context for disambiguation

        Returns:
            Tuple of (merged_entities, merged_relationships)

        Raises:
            EntityMergerError: If merging fails unrecoverably
        """
        ...

    async def resolve_candidates(
        self,
        candidates: list[EntityMergeCandidate],
    ) -> list[EntityMergeDecision]:
        """Resolve merge candidates, potentially using LLM assistance.

        Args:
            candidates: List of candidate pairs to evaluate

        Returns:
            List of merge decisions corresponding to each candidate
        """
        ...
