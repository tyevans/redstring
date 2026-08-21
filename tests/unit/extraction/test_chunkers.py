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
    @settings(max_examples=60)
    def test_chunking_terminates_and_no_chunk_exceeds_its_budget(self, text, chunk_size):
        """Termination is the property, and hypothesis is the way to be sure of it.

        A large example is legitimately slow, and the failure mode being
        guarded against is a *hang* rather than a slow example. Deadlines are
        off suite-wide (`tests/conftest.py`), so nothing here has to say so.
        """
        chunker = SlidingWindowChunker(default_chunk_size=chunk_size, default_overlap=50)

        result = chunker.chunk(text)

        assert all(produced.length <= chunk_size for produced in result.chunks)
        assert result.total_chunks == len(result.chunks)

    @given(text=texts(min_size=1))
    @settings(max_examples=60)
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

    def test_a_tail_shorter_than_the_minimum_chunk_is_still_emitted(self):
        """The dropped tail the property above can only find by luck.

        `_generate_chunks` used to stop once the unconsumed remainder was
        shorter than `min_chunk_size`, under a comment claiming the last
        chunk already included it. It did not: the final characters were
        never emitted by anything, and a document's closing sentence is a
        span extraction simply never sees.

        The property *can* reach this -- with `overlap=40` and
        `min_chunk_size=100` a remainder under 60 characters triggers it --
        and across 60 sampled examples it did not, which is this project's
        standing rule about pinning a boundary as an example rather than
        trusting a sampler.
        """
        text = "word " * 1000 + "the tail"
        chunker = SlidingWindowChunker(default_chunk_size=1000, default_overlap=0)

        result = chunker.chunk(text)

        assert "".join(produced.text for produced in result.chunks) == text

    @given(text=texts(min_size=400))
    @settings(max_examples=60)
    def test_no_chunk_is_wholly_contained_in_another(self, text):
        """A redundant chunk costs a row and an embedding and adds no reach.

        `end` is clamped to `len(text)`, so a chunk can finish at the end of
        the document -- but the loop's exit test is on `next_start`, which is
        `end - overlap` and so still `overlap` short. One more window was
        emitted at `(len(text) - overlap, len(text))`, wholly inside the chunk
        just yielded.

        It buys nothing: every term in it is already in the containing chunk,
        so it adds no retrieval reach, while costing a row, an embedding call,
        and -- for a consumer that aggregates by max -- a second draw for the
        tail's terms that no mid-document span gets. A consumer deduplicating
        passages by offset cannot collapse it either, because the two spans
        differ.

        Property rather than example because the containment is a fact about
        the loop's exit condition and not about any one length; the pinned
        example below is the boundary this project's rule says not to leave to
        a sampler.

        **`min_size=400` is load-bearing.** The first version of this took
        `texts(min_size=1)` like its neighbours and **passed against the
        defect**: the loop only emits a redundant window for a text longer
        than the chunk size, and across 60 samples from a 1-to-4000 range
        hypothesis produced nothing over 300. A property that cannot reach the
        case it names is worse than no property, because it reads as coverage.
        """
        result = SlidingWindowChunker(default_chunk_size=300, default_overlap=40).chunk(text)

        spans = [(produced.start_char, produced.end_char) for produced in result.chunks]
        contained = [
            (inner, outer)
            for inner in spans
            for outer in spans
            if inner != outer and outer[0] <= inner[0] and inner[1] <= outer[1]
        ]

        assert contained == []

    @pytest.mark.parametrize("length", [1350, 1800, 2700, 4500])
    def test_a_document_longer_than_the_window_has_no_redundant_tail(self, length):
        """The boundary, pinned, at the settings the defect was measured on.

        Measured 2026-08-21 at 1000/500 across 450-4500 characters: exactly one
        redundant chunk, always the last, for every document longer than the
        window, and none at or under it. The property above samples at 300/40
        and would find this too -- this is here because the numbers in the
        measurement are the ones a reader will want to reproduce, and because
        the two candidate loop exits ("stop when nothing remains" and "stop
        when a chunk reached the end") agree on every text at or under the
        window size, which is where a hand-picked example would most likely
        land.
        """
        text = "The quick brown fox jumps over the lazy dog. " * (length // 44)
        result = SlidingWindowChunker(default_chunk_size=1000, default_overlap=500).chunk(text)

        ends = [produced.end_char for produced in result.chunks]

        assert ends == sorted(set(ends)), f"a chunk ends where an earlier one did: {ends}"

    @given(text=texts(min_size=1))
    @settings(max_examples=60)
    def test_chunking_the_same_text_twice_gives_the_same_chunks(self, text):
        chunker = SlidingWindowChunker(default_chunk_size=300, default_overlap=40)

        assert chunker.chunk(text).chunks == chunker.chunk(text).chunks

    @given(text=texts(min_size=1))
    @settings(max_examples=60)
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
