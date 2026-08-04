"""Chunkers: splitting a document into pieces one model call can handle.

Concrete implementations of `kg_builder.extraction.protocols.Chunker`:

- `SlidingWindowChunker`: fixed-size windows with configurable overlap.

Construct one directly. The decorator registry that used to stand in front of
these went with `preprocessing/factory.py`: a registry earns its keep when the
implementation is named by a string from outside the process, and here it is
chosen by a caller who is already importing the class.
"""

from kg_builder.extraction.chunkers.sliding_window_chunker import SlidingWindowChunker

__all__ = [
    "SlidingWindowChunker",
]
