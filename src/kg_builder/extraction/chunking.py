"""What a chunker produces and what a merger is asked to decide.

Three types came over from `preprocessing/schemas.py` and did not stay:
`PreprocessingResult` (sourcing, removed in slice 1), `PipelineMetrics` (the
pipeline it timed is gone), and `LLMMergeResponse`, which was shadowed by an
identically named class inside `mergers/llm_merger.py` and so had never been
the one in use.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


@dataclass
class Chunk:
    """One piece of a document, and where in the document it came from.

    `start_char`/`end_char` index the *original* text, which is what keeps an
    entity traceable back to the passage that produced it once merging has
    discarded which chunk reported it.
    """

    text: str
    chunk_index: int
    start_char: int
    end_char: int
    overlap_with_previous: int = 0
    metadata: dict = field(default_factory=dict)

    @property
    def length(self) -> int:
        """Chunk length in characters."""
        return len(self.text)


@dataclass
class ChunkingResult:
    """Every chunk of one document, plus how it was split."""

    chunks: list[Chunk]
    total_chunks: int
    original_length: int
    chunking_method: str = ""
    overlap_size: int = 0


class EntityMergeCandidate(BaseModel):
    """Two entities that might denote the same thing, and how alike they look."""

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
    """Whether one candidate pair should merge, and into what."""

    should_merge: bool = Field(description="Whether the entities should be merged")
    merged_name: str | None = Field(default=None, description="Canonical name to use if merging")
    merged_type: str | None = Field(default=None, description="Type to use if merging")
    confidence: float = Field(default=0.0, description="Confidence in the decision (0-1)")
    reasoning: str | None = Field(default=None, description="Explanation of the decision")
