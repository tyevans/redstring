"""What a chunker produces.

Five types came over from `preprocessing/schemas.py` and did not stay.
`PreprocessingResult` was sourcing, removed in slice 1. `PipelineMetrics`
timed a pipeline that is gone. `LLMMergeResponse` was shadowed by an
identically named class inside `mergers/llm_merger.py` and so had never been
the one in use. `EntityMergeCandidate` and `EntityMergeDecision` described a
dict-shaped merge protocol that `kg_builder.extraction.merging` replaces and
that slice 7 redesigned on domain types -- see `kg_builder.consolidation`.
"""

from dataclasses import dataclass, field
from typing import Any


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
    metadata: dict[str, Any] = field(default_factory=dict)

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
