"""
Chunker implementations for document splitting.

This module provides concrete implementations of the Chunker protocol:
- SlidingWindowChunker: Fixed-size windows with configurable overlap

Chunkers are registered via decorators and created via ChunkerFactory.
"""

# Import implementations to trigger registration
from kg_builder.preprocessing.chunkers.sliding_window_chunker import SlidingWindowChunker

__all__ = [
    "SlidingWindowChunker",
]
