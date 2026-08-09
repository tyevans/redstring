"""Chunkers: splitting a document into pieces one model call can handle.

Concrete implementations of `redstring.extraction.protocols.Chunker`:

- `SlidingWindowChunker`: fixed-size windows with configurable overlap.
- `BoundaryPreferenceChunker`: the same cascade, but searching the whole
  window for a boundary rather than its last 500 characters, and losing no
  characters. Prefer it when the passages will be quoted back to a reader;
  `SlidingWindowChunker` remains the default everywhere, because chunk ids
  are content-addressed and switching re-keys every chunk of every
  re-ingested document.

Construct one directly. The decorator registry that used to stand in front of
these went with `preprocessing/factory.py`: a registry earns its keep when the
implementation is named by a string from outside the process, and here it is
chosen by a caller who is already importing the class.
"""

from redstring.extraction.chunkers.boundary_preference_chunker import BoundaryPreferenceChunker
from redstring.extraction.chunkers.sliding_window_chunker import SlidingWindowChunker

__all__ = [
    "BoundaryPreferenceChunker",
    "SlidingWindowChunker",
]
