"""The sliding-window chunker, which arrived from `preprocessing/` untested.

`preprocessing/` had no test directory at all, so nothing here is a port of an
existing test. Two areas get the most weight, because they are where a chunker
fails expensively rather than visibly:

- **termination.** The loop's next start is derived from a break point the
  text chooses, so a break point that does not advance is an infinite loop.
  CLAUDE.md's rule about bounding adapter-driven loops applies: a hanging test
  reads as CI trouble and gets retried rather than investigated.
- **coverage of the input.** A chunker that silently drops a span loses
  entities with no error anywhere -- the extraction simply finds less, which
  is indistinguishable from a document that said less.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from redstring.extraction.chunkers import SlidingWindowChunker
from redstring.extraction.errors import ChunkSizeError


def texts(min_size: int = 1, max_size: int = 4000) -> st.SearchStrategy[str]:
    """Prose-ish text: letters, spaces, stops and newlines, as a chunker meets it."""
    return st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz .!?\n"),
        min_size=min_size,
        max_size=max_size,
    )


class TestConfiguration:
    def test_an_overlap_at_least_as_large_as_the_chunk_is_refused_at_construction(self):
        """The configuration that cannot terminate.

        Each window would start no later than the previous one did, so the
        loop cannot advance. Refusing it here is what makes the hang
        impossible rather than merely unlikely.
        """
        with pytest.raises(ChunkSizeError):
            SlidingWindowChunker(default_chunk_size=100, default_overlap=100)

    def test_the_same_configuration_is_refused_per_call(self):
        """A chunker built with sane defaults can still be asked for nonsense."""
        chunker = SlidingWindowChunker(default_chunk_size=1000, default_overlap=100)

        with pytest.raises(ChunkSizeError):
            chunker.chunk("some text", max_chunk_size=50, overlap_size=50)

    def test_a_negative_overlap_is_refused(self):
        with pytest.raises(ChunkSizeError):
            SlidingWindowChunker(default_chunk_size=1000, default_overlap=-1)

    def test_a_chunk_smaller_than_the_minimum_is_refused(self):
        with pytest.raises(ChunkSizeError):
            SlidingWindowChunker(default_chunk_size=50, default_overlap=0, min_chunk_size=100)


class TestEdges:
    def test_blank_text_yields_no_chunks_rather_than_one_empty_chunk(self):
        """An empty chunk is a model call that can only waste tokens."""
        result = SlidingWindowChunker().chunk("   \n\t  ")

        assert result.chunks == []
        assert result.total_chunks == 0

    def test_text_that_fits_is_one_chunk_covering_all_of_it(self):
        text = "Ada Lovelace worked with Charles Babbage."

        [only] = SlidingWindowChunker(default_chunk_size=1000).chunk(text).chunks

        assert only.text == text
        assert (only.start_char, only.end_char) == (0, len(text))
        assert only.overlap_with_previous == 0

    def test_a_single_chunk_reports_no_overlap_however_the_chunker_was_configured(self):
        """There is no previous chunk to overlap with, whatever the setting says."""
        result = SlidingWindowChunker(default_chunk_size=1000, default_overlap=200).chunk("short")

        assert result.overlap_size == 0


class TestSplitting:
    def test_long_text_becomes_several_chunks(self):
        text = "word " * 2000

        result = SlidingWindowChunker(default_chunk_size=1000, default_overlap=100).chunk(text)

        assert result.total_chunks > 1
        assert result.original_length == len(text)

    def test_a_chunk_is_exactly_the_span_it_claims(self):
        """Offsets index the original text, and are what makes an entity traceable.

        Checked against the source rather than against the chunk's own
        `length`, which would be trivially self-consistent.
        """
        text = "sentence one. sentence two. " * 200

        result = SlidingWindowChunker(default_chunk_size=500, default_overlap=50).chunk(text)

        for produced in result.chunks:
            assert produced.text == text[produced.start_char : produced.end_char]

    def test_consecutive_chunks_actually_overlap(self):
        """The point of overlap: a sentence on a boundary survives somewhere whole.

        Without this the chunker is a plain splitter, every boundary sentence
        is cut in half, and the merger has nothing to deduplicate because
        neither half names the entity.
        """
        text = "word " * 2000

        result = SlidingWindowChunker(default_chunk_size=1000, default_overlap=200).chunk(text)

        for earlier, later in pairwise(result.chunks):
            assert later.start_char < earlier.end_char

    def test_chunk_indices_are_consecutive_from_zero(self):
        result = SlidingWindowChunker(default_chunk_size=400, default_overlap=50).chunk(
            "some words here. " * 200
        )

        assert [c.chunk_index for c in result.chunks] == list(range(result.total_chunks))


class TestProperties:
    @given(text=texts(), chunk_size=st.integers(min_value=100, max_value=2000))
    @settings(max_examples=60, deadline=None)
    def test_chunking_terminates_and_no_chunk_exceeds_its_budget(self, text, chunk_size):
        """Termination is the property, and hypothesis is the way to be sure of it.

        `deadline=None` because a large example is legitimately slow; the
        failure mode being guarded against is a *hang*, which pytest's own
        timeout surfaces, not a slow example.
        """
        chunker = SlidingWindowChunker(default_chunk_size=chunk_size, default_overlap=50)

        result = chunker.chunk(text)

        assert all(produced.length <= chunk_size for produced in result.chunks)
        assert result.total_chunks == len(result.chunks)

    @given(text=texts(min_size=1))
    @settings(max_examples=60, deadline=None)
    def test_every_character_of_the_input_lands_in_some_chunk(self, text):
        """A silently dropped span loses entities with no error anywhere.

        Stated as coverage of the *offsets* rather than by reassembling the
        text, because overlap means concatenating the chunks does not give
        the input back -- a reassembly test would have to encode the overlap
        arithmetic it is supposed to be checking.
        """
        result = SlidingWindowChunker(default_chunk_size=300, default_overlap=40).chunk(text)
        if not result.chunks:
            return

        covered = set()
        for produced in result.chunks:
            covered.update(range(produced.start_char, produced.end_char))

        assert covered == set(range(len(text)))

    @given(text=texts(min_size=1))
    @settings(max_examples=60, deadline=None)
    def test_chunking_the_same_text_twice_gives_the_same_chunks(self, text):
        chunker = SlidingWindowChunker(default_chunk_size=300, default_overlap=40)

        assert chunker.chunk(text).chunks == chunker.chunk(text).chunks

    @given(text=texts(min_size=1))
    @settings(max_examples=60, deadline=None)
    def test_chunks_are_ordered_and_start_at_the_beginning(self, text):
        result = SlidingWindowChunker(default_chunk_size=300, default_overlap=40).chunk(text)
        if not result.chunks:
            return

        assert result.chunks[0].start_char == 0
        starts = [produced.start_char for produced in result.chunks]
        assert starts == sorted(starts)


def test_the_chunker_reports_the_type_the_protocol_asks_for():
    assert SlidingWindowChunker().chunker_type == "sliding_window"


def test_a_chunker_satisfies_the_protocol():
    from redstring.extraction.protocols import Chunker

    assert isinstance(SlidingWindowChunker(), Chunker)
