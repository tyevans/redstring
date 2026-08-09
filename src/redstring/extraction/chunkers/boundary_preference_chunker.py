"""A chunker that cuts at the best boundary in the whole window, and loses nothing.

Contributed by the `research-team` project, which had written it as a private
copy after finding `SlidingWindowChunker`'s boundaries too coarse for
*citation quality*: a chunk that ends mid-sentence produces a quotation nobody
can use. Upstreaming it is what stops that copy becoming a fork -- see
BACKLOG B120.

Both chunkers cascade paragraph -> sentence -> word -> hard cut, so the
difference is not the preference order. It is three things:

- **How far back a boundary may be.** `SlidingWindowChunker` searches the last
  500 characters of the window. At its own default size of 3000 that is a
  sixth of the chunk, so a paragraph break 600 characters back is invisible
  and the cut lands mid-word instead. Here the whole window is in reach:
  the ceiling is the only bound, and a document with sparse punctuation
  yields *shorter* chunks rather than worse boundaries.
- **What counts as a sentence end.** `[.!?]\\s+(?=[A-Z])` requires the next
  sentence to begin with a capital, so it misses a sentence ending the text,
  one followed by a quoted or parenthesised continuation, and every sentence
  in a lowercase-initial language. Here a terminator may be followed by
  closing quotes or brackets, and by end-of-text as well as whitespace.
- **Cost.** Boundaries are found once for the document and located by
  bisection, rather than re-scanned per chunk. The scan is linear either way;
  the re-scan is what makes chunking quadratic on the long documents this
  exists to serve.

Two rules the implementation holds to, because the offsets are what make a
passage citable:

**Chunk text is never stripped.** A separator stays attached to the chunk it
ends. Trimming it would make the partition lossy, and every offset after the
lost character would point at the wrong words.

**Boundary detection may only choose among split points, never rewrite text.**
So CRLF stays CRLF.

"Sentence" is a heuristic and not a claim to sentence segmentation. It splits
"et al. 1999" and "Fig. 4", and it does not split at "3.5" or at an
unpunctuated line ending. That is an acceptable trade for a dependency-free
chunker: a misplaced boundary costs a chunk that reads slightly oddly, while a
lost character costs correctness.
"""

from __future__ import annotations

import re
from bisect import bisect_right

from redstring.extraction.chunking import Chunk, ChunkingResult
from redstring.extraction.errors import ChunkSizeError

#: Runs of whitespace: the only places a split can avoid landing mid-word.
_WHITESPACE = re.compile(r"\s+")

#: A sentence-ish terminator; see the module docstring for what this misses.
_SENTENCE = re.compile("[.!?][\"')\\]\\u2019\\u201d]*(?:\\s+|$)")


def _boundaries(text: str) -> tuple[list[int], list[int], list[int]]:
    """Every candidate split point in `text`, as three ascending lists.

    Computed once for the whole document rather than per chunk.
    """
    paragraphs: list[int] = []
    words: list[int] = []
    for run in _WHITESPACE.finditer(text):
        words.append(run.end())
        if run.group().count("\n") >= 2:
            paragraphs.append(run.end())
    sentences = [match.end() for match in _SENTENCE.finditer(text)]
    return paragraphs, sentences, words


def _last_within(candidates: list[int], after: int, limit: int) -> int | None:
    """The greatest candidate in `(after, limit]`, or `None`."""
    index = bisect_right(candidates, limit) - 1
    if index >= 0 and candidates[index] > after:
        return candidates[index]
    return None


class BoundaryPreferenceChunker:
    """Splits at the last clean boundary at or before the size ceiling.

    `max_chunk_size` is a ceiling, not an average: a chunk is cut at the last
    acceptable boundary at or before it. Only a token longer than the ceiling
    is ever cut mid-word, because refusing to cut it would let one
    pathological run defeat the bound entirely.

    With `overlap_size=0` the chunks are a partition of the input: ordered,
    contiguous, and concatenating to exactly the text handed in. A positive
    overlap repeats trailing characters at the start of the next chunk, so the
    chunks still cover every character but no longer partition.

    Example:
        chunker = BoundaryPreferenceChunker(default_chunk_size=1200)
        result = chunker.chunk(long_text)
    """

    def __init__(
        self,
        default_chunk_size: int = 3000,
        default_overlap: int = 200,
    ) -> None:
        """Configure the defaults `chunk` uses when a call does not override them.

        Args:
            default_chunk_size: Default maximum characters per chunk.
            default_overlap: Default characters shared with the previous chunk.

        Raises:
            ChunkSizeError: The pair cannot terminate, or either is negative.
        """
        self._validate(default_chunk_size, default_overlap)
        self._default_chunk_size = default_chunk_size
        self._default_overlap = default_overlap

    @staticmethod
    def _validate(chunk_size: int, overlap: int) -> None:
        if chunk_size < 1:
            raise ChunkSizeError(f"chunk size ({chunk_size}) must be at least 1")
        if overlap < 0:
            raise ChunkSizeError(f"overlap ({overlap}) must be >= 0")
        if overlap >= chunk_size:
            # Otherwise a chunk could rewind at least as far as it advanced.
            raise ChunkSizeError(f"overlap ({overlap}) must be < chunk size ({chunk_size})")

    @property
    def chunker_type(self) -> str:
        """Return the type identifier for this chunker."""
        return "boundary_preference"

    def chunk(
        self,
        text: str,
        max_chunk_size: int | None = None,
        overlap_size: int | None = None,
    ) -> ChunkingResult:
        """Split `text` at the best boundary within each window.

        Args:
            text: Text to split.
            max_chunk_size: Maximum characters per chunk; the default when None.
            overlap_size: Characters shared with the previous chunk; the
                default when None.

        Returns:
            A `ChunkingResult`. Blank text -- empty or whitespace only --
            yields zero chunks, as `Chunker` requires: a blank chunk is a
            model call that can only waste tokens. That is the one input for
            which the chunks are not a partition of the text.

        Raises:
            ChunkSizeError: The requested size and overlap are incompatible.
        """
        chunk_size = self._default_chunk_size if max_chunk_size is None else max_chunk_size
        overlap = self._default_overlap if overlap_size is None else overlap_size
        self._validate(chunk_size, overlap)

        if not text.strip():
            return ChunkingResult(
                chunks=[],
                total_chunks=0,
                original_length=0,
                chunking_method=self.chunker_type,
                overlap_size=overlap,
            )

        paragraphs, sentences, words = _boundaries(text)
        chunks: list[Chunk] = []
        start = 0
        previous_end = 0
        while start < len(text):
            limit = start + chunk_size
            if limit >= len(text):
                end = len(text)
            else:
                end = (
                    _last_within(paragraphs, start, limit)
                    or _last_within(sentences, start, limit)
                    or _last_within(words, start, limit)
                    # No boundary in reach: cut at the ceiling. Inside a long
                    # run of whitespace this is the right answer, not a
                    # fallback.
                    or limit
                )
            chunks.append(
                Chunk(
                    text=text[start:end],
                    chunk_index=len(chunks),
                    start_char=start,
                    end_char=end,
                    overlap_with_previous=previous_end - start if chunks else 0,
                )
            )
            if end == len(text):
                # Reaching the end is what ends the loop, not `start`
                # overtaking it. Without this the rewind applies to the final
                # chunk too, so `start` creeps forward one character at a
                # time and the tail comes out as a run of slivers -- a
                # 17-character document at the default overlap yields
                # seventeen chunks. Every character is still covered, so
                # neither a coverage property nor a reassembly property sees
                # it; only asserting the *chunk count* does.
                break
            previous_end = end
            # `start + 1` guarantees progress even when a boundary lands close
            # enough behind `end` that the overlap would otherwise rewind past
            # it. Every candidate is strictly greater than `start`, and the
            # hard cut is `start + chunk_size`, so `end > start` always.
            start = max(start + 1, end - overlap)

        return ChunkingResult(
            chunks=chunks,
            total_chunks=len(chunks),
            original_length=len(text),
            chunking_method=self.chunker_type,
            overlap_size=overlap,
        )
