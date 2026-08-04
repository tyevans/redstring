"""Errors raised while splitting a document and merging what its chunks yielded.

Trimmed on the way over from `preprocessing/`. Gone with the move:
`PreprocessorError` (preprocessing here meant HTML boilerplate removal, which
is sourcing, which slice 1 removed from this library's scope),
`ChunkerNotRegisteredError` / `EntityMergerNotRegisteredError` (there is no
registry left for a lookup to miss), and `PipelineError` /
`PipelineConfigError` (the config object they described went with
`preprocessing/pipeline.py`). None had a reference outside its own definition.

`EntityMergerError` followed them: `kg_builder.extraction.merging` is a total
function over already-mapped results and has no failure mode to name.

These stay separate from `KgBuilderError` deliberately: they are raised by
extraction's own machinery, not by a port, and the domain's error hierarchy is
what callers catch across the port boundary.
"""


class ChunkingError(Exception):
    """Base class for failures in splitting a document or merging its results."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.message = message
        self.cause = cause


class ChunkerError(ChunkingError):
    """A chunker could not split the text it was given."""


class ChunkSizeError(ChunkerError):
    """Chunk size and overlap cannot both be satisfied.

    Raised at construction *and* per call, because a chunker built with a
    valid default can still be asked for an impossible one. Overlap at least
    as large as the chunk size is the case that matters: it makes each window
    start no later than the previous one did, so the loop cannot advance.
    """
