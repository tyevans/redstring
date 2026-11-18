"""Named maxima for chunk size and overlap, and the one check that enforces them.

Both chunkers accept a size and an overlap at construction and again on every
`chunk` call. Each value multiplies work: the chunk count is roughly the
document length divided by the step (`size - overlap`), and each chunk becomes
one model call. Without an upper bound a caller can ask for a window far
larger than any model accepts, or for an overlap that leaves a one-character
step.

The maxima are backstops. They do not say what size is good; that depends on
the model's context window and is the caller's choice.
"""

from __future__ import annotations

from typing import Final

from redstring.extraction.errors import ChunkSizeError

__all__ = ["MAX_CHUNK_SIZE", "MAX_OVERLAP_SIZE", "check_window"]

#: The largest chunk, in characters. Roughly 50 000 tokens for English text,
#: which is past the context window of most models this library targets.
MAX_CHUNK_SIZE: Final = 200_000

#: The largest overlap, in characters. A quarter of `MAX_CHUNK_SIZE`, so the
#: step between chunks stays at least three quarters of the window.
MAX_OVERLAP_SIZE: Final = 50_000


def check_window(
    chunk_size: int,
    overlap: int,
    *,
    size_name: str,
    overlap_name: str,
    min_chunk_size: int = 1,
) -> None:
    """Refuse a size and overlap pair that cannot be chunked.

    Args:
        chunk_size: Characters per chunk.
        overlap: Characters shared with the previous chunk.
        size_name: Parameter name to show in the error for `chunk_size`.
        overlap_name: Parameter name to show in the error for `overlap`.
        min_chunk_size: Floor for `chunk_size`.

    Raises:
        ChunkSizeError: A value is outside its range, or the overlap is not
            smaller than the chunk size.
    """
    if chunk_size < min_chunk_size:
        raise ChunkSizeError(
            f"{size_name} ({chunk_size}) must be >= min_chunk_size ({min_chunk_size})"
        )
    if chunk_size > MAX_CHUNK_SIZE:
        raise ChunkSizeError(f"{size_name} ({chunk_size}) must be <= {MAX_CHUNK_SIZE}")
    if overlap < 0:
        raise ChunkSizeError(f"{overlap_name} ({overlap}) must be >= 0")
    if overlap > MAX_OVERLAP_SIZE:
        raise ChunkSizeError(f"{overlap_name} ({overlap}) must be <= {MAX_OVERLAP_SIZE}")
    if overlap >= chunk_size:
        raise ChunkSizeError(f"{overlap_name} ({overlap}) must be < {size_name} ({chunk_size})")
