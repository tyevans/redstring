"""Mergers: resolving one entity that several chunks each found separately.

Concrete implementations of `kg_builder.extraction.protocols.EntityMerger`:

- `SimpleMerger`: exact and fuzzy name matching, no model call.
- `LLMMerger`: `SimpleMerger` plus a model for the ambiguous middle band.

Merging exists **because** chunking does. A document too long for one call is
split, and every chunk that mentions a thing reports it again -- so the
chunker manufactures the duplicates the merger then removes. That the two
lived in `preprocessing/` while extraction lived elsewhere hid the fact that
they are one loop.
"""

from kg_builder.extraction.mergers.llm_merger import LLMMerger
from kg_builder.extraction.mergers.simple_merger import SimpleMerger

__all__ = [
    "LLMMerger",
    "SimpleMerger",
]
