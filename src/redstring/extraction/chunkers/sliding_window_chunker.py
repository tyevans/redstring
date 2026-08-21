"""
Sliding window text chunker with sentence boundary awareness.

Provides configurable chunking with overlap to maintain context
across chunk boundaries. Attempts to respect natural text boundaries
(paragraphs, sentences, words) when splitting.
"""

import logging
import re
from collections.abc import Iterator

from redstring.extraction.chunking import Chunk, ChunkingResult
from redstring.extraction.errors import ChunkSizeError

logger = logging.getLogger(__name__)


class SlidingWindowChunker:
    """Chunker that uses sliding windows with configurable overlap.

    Features:
    - Configurable chunk size and overlap
    - Sentence boundary awareness (tries not to split mid-sentence)
    - Paragraph boundary preference
    - Handles edge cases (short documents, empty text)

    The chunker uses a priority system for finding break points:
    1. Paragraph breaks (double newlines)
    2. Sentence endings (., !, ?)
    3. Word boundaries (spaces)
    4. Hard cutoff (if no boundaries found)

    Attributes:
        default_chunk_size: Default maximum characters per chunk
        default_overlap: Default overlap between chunks
        respect_sentence_boundaries: Whether to avoid mid-sentence splits
        respect_paragraph_boundaries: Whether to prefer paragraph breaks
        min_chunk_size: Floor on `default_chunk_size`, refused at construction

    Example:
        chunker = SlidingWindowChunker(default_chunk_size=3000, default_overlap=200)
        result = chunker.chunk(long_text)
        for chunk in result.chunks:
            print(f"Chunk {chunk.chunk_index}: {chunk.length} chars")
    """

    # Regex patterns for boundary detection
    SENTENCE_ENDINGS = re.compile(r"([.!?])\s+(?=[A-Z])")
    PARAGRAPH_BREAKS = re.compile(r"\n\s*\n")

    def __init__(
        self,
        default_chunk_size: int = 3000,
        default_overlap: int = 200,
        respect_sentence_boundaries: bool = True,
        respect_paragraph_boundaries: bool = True,
        min_chunk_size: int = 100,
    ) -> None:
        """Initialize sliding window chunker.

        Args:
            default_chunk_size: Default maximum characters per chunk (default: 3000)
            default_overlap: Default overlap between chunks (default: 200)
            respect_sentence_boundaries: Try not to split mid-sentence (default: True)
            respect_paragraph_boundaries: Prefer paragraph breaks as boundaries (default: True)
            min_chunk_size: Floor on `default_chunk_size` (default: 100). It
                bounds the *configuration* and nothing else -- a final chunk
                shorter than this is emitted rather than dropped.

        Raises:
            ChunkSizeError: If configuration is invalid
        """
        if default_chunk_size < min_chunk_size:
            raise ChunkSizeError(
                f"default_chunk_size ({default_chunk_size}) must be "
                f">= min_chunk_size ({min_chunk_size})"
            )
        if default_overlap >= default_chunk_size:
            raise ChunkSizeError(
                f"default_overlap ({default_overlap}) must be "
                f"< default_chunk_size ({default_chunk_size})"
            )
        if default_overlap < 0:
            raise ChunkSizeError(f"default_overlap ({default_overlap}) must be >= 0")

        self._default_chunk_size = default_chunk_size
        self._default_overlap = default_overlap
        self._respect_sentences = respect_sentence_boundaries
        self._respect_paragraphs = respect_paragraph_boundaries
        self._min_chunk_size = min_chunk_size

        logger.info(
            "SlidingWindowChunker initialized",
            extra={
                "default_chunk_size": default_chunk_size,
                "default_overlap": default_overlap,
                "respect_sentences": respect_sentence_boundaries,
                "respect_paragraphs": respect_paragraph_boundaries,
            },
        )

    @property
    def chunker_type(self) -> str:
        """Return the type identifier for this chunker."""
        return "sliding_window"

    def chunk(
        self,
        text: str,
        max_chunk_size: int | None = None,
        overlap_size: int | None = None,
    ) -> ChunkingResult:
        """Split text into overlapping chunks.

        Args:
            text: Text to chunk
            max_chunk_size: Maximum chunk size (uses default if None)
            overlap_size: Overlap size (uses default if None)

        Returns:
            ChunkingResult with list of chunks

        Raises:
            ChunkSizeError: If provided configuration is invalid
        """
        chunk_size = max_chunk_size or self._default_chunk_size
        overlap = overlap_size if overlap_size is not None else self._default_overlap
        original_length = len(text)

        # Validate runtime configuration
        if overlap >= chunk_size:
            raise ChunkSizeError(
                f"overlap_size ({overlap}) must be < max_chunk_size ({chunk_size})"
            )

        # Handle edge cases
        if not text or not text.strip():
            logger.debug("Empty text provided, returning empty result")
            return ChunkingResult(
                chunks=[],
                total_chunks=0,
                original_length=0,
                chunking_method=self.chunker_type,
                overlap_size=overlap,
            )

        # Single chunk case
        if len(text) <= chunk_size:
            logger.debug(
                "Text fits in single chunk",
                extra={"text_length": len(text), "chunk_size": chunk_size},
            )
            return ChunkingResult(
                chunks=[
                    Chunk(
                        text=text,
                        chunk_index=0,
                        start_char=0,
                        end_char=len(text),
                        overlap_with_previous=0,
                    )
                ],
                total_chunks=1,
                original_length=original_length,
                chunking_method=self.chunker_type,
                overlap_size=0,
            )

        # Generate chunks
        chunks = list(self._generate_chunks(text, chunk_size, overlap))

        logger.info(
            "Chunking complete",
            extra={
                "original_length": original_length,
                "total_chunks": len(chunks),
                "chunk_size": chunk_size,
                "overlap": overlap,
                "avg_chunk_length": sum(c.length for c in chunks) // len(chunks) if chunks else 0,
            },
        )

        return ChunkingResult(
            chunks=chunks,
            total_chunks=len(chunks),
            original_length=original_length,
            chunking_method=self.chunker_type,
            overlap_size=overlap,
        )

    def _generate_chunks(
        self,
        text: str,
        chunk_size: int,
        overlap: int,
    ) -> Iterator[Chunk]:
        """Generate chunks from text.

        Args:
            text: Text to chunk
            chunk_size: Maximum chunk size
            overlap: Overlap between chunks

        Yields:
            Chunk objects
        """
        start = 0
        chunk_index = 0
        text_len = len(text)

        while start < text_len:
            # Calculate tentative end position
            end = min(start + chunk_size, text_len)

            # If not at the end of text, find a good break point
            if end < text_len:
                end = self._find_break_point(text, start, end)

            # Extract chunk text
            chunk_text = text[start:end]

            # Calculate overlap with previous chunk
            overlap_with_prev = min(overlap, start) if chunk_index > 0 else 0

            yield Chunk(
                text=chunk_text,
                chunk_index=chunk_index,
                start_char=start,
                end_char=end,
                overlap_with_previous=overlap_with_prev,
            )

            # Nothing is left to cover once a chunk reaches the end of the
            # text, and the exit test below cannot see that: it is on
            # `next_start`, which is `end - overlap` and so still `overlap`
            # short of the end. Without this, every text longer than the
            # window got one final window at `(text_len - overlap, text_len)`
            # -- wholly inside the chunk just yielded.
            #
            # That chunk bought nothing. Every term in it is already in the
            # containing chunk, so it adds no retrieval reach, while costing a
            # row, an embedding call, and -- for a consumer aggregating by max
            # -- a second draw for the tail's terms that no mid-document span
            # gets. A consumer deduplicating passages by offset cannot collapse
            # it either, because the two spans differ.
            #
            # **This does not drop a tail**, which is the failure the comment
            # below records and the one an over-eager fix here would
            # reintroduce: the chunk just yielded ends at `text_len`, so the
            # final characters have been emitted. What that comment guards is
            # stopping before emitting a short *remainder*; this stops after
            # emitting the remainder-containing chunk.
            if end >= text_len:
                break

            # Calculate next start position
            next_start = end - overlap

            # Prevent infinite loop if overlap is too large relative to progress
            if next_start <= start:
                next_start = end

            # Done once nothing is left. A short remainder is still emitted:
            # this used to stop early when it was shorter than
            # `min_chunk_size`, under a comment claiming the last chunk
            # already covered it. It did not -- the tail was silently
            # dropped, which loses a document's closing sentence with no
            # error anywhere. A small final chunk is a cheap model call;
            # extending the previous one instead would push a chunk past the
            # size ceiling the caller asked for, which is worse.
            if text_len - next_start <= 0:
                break

            start = next_start
            chunk_index += 1

    def _find_break_point(
        self,
        text: str,
        start: int,
        end: int,
    ) -> int:
        """Find a good break point near the end position.

        Prefers (in order):
        1. Paragraph breaks (double newlines)
        2. Sentence endings (., !, ? followed by capital letter)
        3. Single newlines
        4. Word boundaries (spaces)
        5. Hard cutoff at end position

        Args:
            text: Full text
            start: Start position of current chunk
            end: Target end position

        Returns:
            Actual end position (may be before target)
        """
        # Search in the last portion of the chunk for a good break
        search_window = min(500, end - start)  # Look back up to 500 chars
        search_start = max(start, end - search_window)
        segment = text[search_start:end]

        # Try paragraph break first (strongest boundary)
        if self._respect_paragraphs:
            para_breaks = list(self.PARAGRAPH_BREAKS.finditer(segment))
            if para_breaks:
                last_break = para_breaks[-1]
                return search_start + last_break.end()

        # Try sentence boundary
        if self._respect_sentences:
            sentence_ends = list(self.SENTENCE_ENDINGS.finditer(segment))
            if sentence_ends:
                last_sentence = sentence_ends[-1]
                return search_start + last_sentence.end()

        # Try single newline
        last_newline = segment.rfind("\n")
        if last_newline > len(segment) // 2:  # Only if in latter half
            return search_start + last_newline + 1

        # Fall back to word boundary
        last_space = segment.rfind(" ")
        if last_space > len(segment) // 2:  # Only if in latter half
            return search_start + last_space + 1

        # Hard cutoff - no good boundary found
        return end
