"""The one shape extraction plugs in: `Chunker`.

Two protocols have left this module. `Preprocessor` went first: preprocessing
meant stripping HTML boilerplate, which is sourcing, and slice 1 took sourcing
out of this library -- callers hand it clean text.

`EntityMerger` went with the dict-based mergers it described. Combining what
overlapping chunks each reported is not pluggable and does not need to be:
`entity_id_for` gives two reports of one entity the same id, so
`kg_builder.extraction.merging` is a total function with nothing to configure.
The *fuzzy* resolution the old protocol existed for -- deciding "Ada" and "Ada
Lovelace" are one person -- is consolidation, and belongs to slice 7 where it
produces an auditable `EntitiesMerged`. See BACKLOG B40.
"""

from typing import Protocol, runtime_checkable

from kg_builder.extraction.chunking import ChunkingResult


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
