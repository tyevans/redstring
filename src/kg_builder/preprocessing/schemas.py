"""
Pydantic models and dataclasses for preprocessing results.

This module defines the data structures used throughout the
preprocessing pipeline for type-safe data flow.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


@dataclass
class PreprocessingResult:
    """Result from a preprocessor.

    Attributes:
        clean_text: The extracted/cleaned text content
        metadata: Additional metadata from preprocessing (title, author, etc.)
        original_length: Length of original content in characters
        cleaned_length: Length of cleaned content in characters
        preprocessing_method: Name of the preprocessor used
    """

    clean_text: str
    metadata: dict = field(default_factory=dict)
    original_length: int = 0
    cleaned_length: int = 0
    preprocessing_method: str = ""


@dataclass
class Chunk:
    """A single chunk of text with position metadata.

    Attributes:
        text: The chunk text content
        chunk_index: Zero-based index of this chunk
        start_char: Starting character position in original text
        end_char: Ending character position in original text
        overlap_with_previous: Characters of overlap with previous chunk
        metadata: Additional chunk-specific metadata
    """

    text: str
    chunk_index: int
    start_char: int
    end_char: int
    overlap_with_previous: int = 0
    metadata: dict = field(default_factory=dict)

    @property
    def length(self) -> int:
        """Return chunk length in characters."""
        return len(self.text)


@dataclass
class ChunkingResult:
    """Result from a chunker.

    Attributes:
        chunks: List of text chunks
        total_chunks: Number of chunks produced
        original_length: Length of original text
        chunking_method: Name of the chunker used
        overlap_size: Overlap size used between chunks
    """

    chunks: list[Chunk]
    total_chunks: int
    original_length: int
    chunking_method: str = ""
    overlap_size: int = 0


class EntityMergeCandidate(BaseModel):
    """A candidate pair of entities that might refer to the same thing.

    Used to pass potential duplicates to the entity merger for resolution.
    """

    entity_a_name: str = Field(description="Name of first entity")
    entity_a_type: str = Field(description="Type of first entity")
    entity_a_chunk_index: int = Field(description="Chunk index where entity A was found")
    entity_a_context: str | None = Field(default=None, description="Source text context for A")
    entity_a_description: str | None = Field(default=None, description="Description of entity A")

    entity_b_name: str = Field(description="Name of second entity")
    entity_b_type: str = Field(description="Type of second entity")
    entity_b_chunk_index: int = Field(description="Chunk index where entity B was found")
    entity_b_context: str | None = Field(default=None, description="Source text context for B")
    entity_b_description: str | None = Field(default=None, description="Description of entity B")

    similarity_score: float = Field(default=0.0, description="String similarity score (0-1)")


class EntityMergeDecision(BaseModel):
    """Decision about whether to merge entity candidates.

    Returned by the entity merger after evaluating candidates.
    """

    should_merge: bool = Field(description="Whether the entities should be merged")
    merged_name: str | None = Field(default=None, description="Canonical name to use if merging")
    merged_type: str | None = Field(default=None, description="Type to use if merging")
    confidence: float = Field(default=0.0, description="Confidence in the decision (0-1)")
    reasoning: str | None = Field(default=None, description="Explanation of the decision")


class LLMMergeResponse(BaseModel):
    """Structured response from LLM for merge decisions.

    This is the expected output format from the LLM when resolving
    ambiguous entity pairs.
    """

    decisions: list[dict] = Field(
        description="List of merge decisions for each candidate pair"
    )


@dataclass
class PipelineMetrics:
    """Metrics collected during pipeline execution.

    Useful for monitoring and debugging pipeline performance.
    """

    preprocessing_time_ms: float = 0.0
    chunking_time_ms: float = 0.0
    extraction_time_ms: float = 0.0
    merging_time_ms: float = 0.0
    total_time_ms: float = 0.0

    chunks_processed: int = 0
    chunks_failed: int = 0

    entities_before_merge: int = 0
    entities_after_merge: int = 0
    relationships_before_merge: int = 0
    relationships_after_merge: int = 0

    merge_candidates_found: int = 0
    merge_candidates_resolved: int = 0
