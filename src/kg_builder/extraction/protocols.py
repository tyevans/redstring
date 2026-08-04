"""The two shapes extraction plugs together: `Chunker` and `EntityMerger`.

`Preprocessor` used to be here too. It is gone: preprocessing meant stripping
HTML boilerplate, which is sourcing, and slice 1 took sourcing out of this
library -- callers hand it clean text.

Chunking is synchronous and merging is not, which looks like an inconsistency
and is the real difference between them: splitting a string is CPU work, while
deciding whether two names denote one thing may need a model call.
"""

from typing import Protocol, runtime_checkable

from kg_builder.extraction.chunking import (
    ChunkingResult,
    EntityMergeCandidate,
    EntityMergeDecision,
)


@runtime_checkable
class Chunker(Protocol):
    """Splits a document into pieces small enough for one model call.

    Chunks overlap so that a sentence spanning a boundary survives intact in
    at least one of them. That is also what makes duplicates across chunks the
    normal case rather than a fault, and so why `EntityMerger` exists.
    """

    @property
    def chunker_type(self) -> str:
        """A short identifier for this chunker, recorded on its results."""
        ...

    def chunk(
        self,
        text: str,
        max_chunk_size: int | None = None,
        overlap_size: int | None = None,
    ) -> ChunkingResult:
        """Split `text`.

        Args:
            text: Text to split.
            max_chunk_size: Maximum characters per chunk; the chunker's own
                default when None.
            overlap_size: Characters shared with the previous chunk; the
                chunker's own default when None.

        Returns:
            A `ChunkingResult`. Blank text yields zero chunks rather than one
            empty chunk: a blank chunk is a model call that can only waste
            tokens.

        Raises:
            ChunkSizeError: The requested size and overlap are incompatible.
        """
        ...


@runtime_checkable
class EntityMerger(Protocol):
    """Combines the entities several chunks each reported separately."""

    @property
    def merger_type(self) -> str:
        """A short identifier for this merger, recorded on its results."""
        ...

    async def merge_entities(
        self,
        entities_by_chunk: dict[int, list[dict]],
        relationships_by_chunk: dict[int, list[dict]],
        document_context: str | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Merge per-chunk entities and relationships into one set of each.

        Args:
            entities_by_chunk: chunk index -> entity dicts found in it.
            relationships_by_chunk: chunk index -> relationship dicts.
            document_context: Optional whole-document context for
                disambiguation.

        Returns:
            `(merged_entities, merged_relationships)`.

        Raises:
            EntityMergerError: Merging failed unrecoverably.
        """
        ...

    async def resolve_candidates(
        self,
        candidates: list[EntityMergeCandidate],
    ) -> list[EntityMergeDecision]:
        """Decide each candidate pair: one decision per candidate, in order."""
        ...
