"""The chunker contributed by `research-team`, tested against its own claims.

Its reason for existing is boundary *quality*, so the weight here is on the
three places it differs from `SlidingWindowChunker` -- a boundary far back in
the window, a sentence end the other regex cannot see, and a partition that
loses nothing. A test that only checked "text comes out in pieces" would pass
against the chunker this one was written to improve on, which is the whole
failure shape CLAUDE.md's table is about.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from redstring.extraction.chunkers import BoundaryPreferenceChunker, SlidingWindowChunker
from redstring.extraction.errors import ChunkSizeError
from redstring.extraction.protocols import Chunker


def texts(min_size: int = 1, max_size: int = 4000) -> st.SearchStrategy[str]:
    """Prose-ish text: letters, spaces, stops and newlines, as a chunker meets it."""
    return st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz .!?\n"),
        min_size=min_size,
        max_size=max_size,
    )


class TestConfiguration:
    def test_an_overlap_at_least_as_large_as_the_chunk_is_refused_at_construction(self):
        """The configuration that cannot terminate."""
        with pytest.raises(ChunkSizeError):
            BoundaryPreferenceChunker(default_chunk_size=100, default_overlap=100)

    def test_the_same_configuration_is_refused_per_call(self):
        chunker = BoundaryPreferenceChunker(default_chunk_size=1000, default_overlap=100)

        with pytest.raises(ChunkSizeError):
            chunker.chunk("some text", max_chunk_size=50, overlap_size=50)

    def test_a_negative_overlap_is_refused(self):
        with pytest.raises(ChunkSizeError):
            BoundaryPreferenceChunker(default_chunk_size=1000, default_overlap=-1)

    def test_a_chunk_size_below_one_is_refused(self):
        with pytest.raises(ChunkSizeError):
            BoundaryPreferenceChunker(default_chunk_size=0, default_overlap=0)

    def test_a_zero_chunk_size_per_call_is_refused_rather_than_replaced_by_the_default(self):
        """`max_chunk_size or default` would silently accept 0 as "unset".

        Falsiness is the wrong test for "the caller said nothing": zero is a
        thing the caller can say, and it is nonsense rather than a request
        for the default.
        """
        chunker = BoundaryPreferenceChunker(default_chunk_size=1000, default_overlap=0)

        with pytest.raises(ChunkSizeError):
            chunker.chunk("some text", max_chunk_size=0)

    def test_a_zero_overlap_per_call_overrides_a_nonzero_default(self):
        """The other half of the same distinction: 0 is a value, not an absence."""
        chunker = BoundaryPreferenceChunker(default_chunk_size=40, default_overlap=20)

        result = chunker.chunk("word " * 40, overlap_size=0)

        assert all(produced.overlap_with_previous == 0 for produced in result.chunks)


class TestEdges:
    @pytest.mark.parametrize("text", ["", "   ", "\n\n\t "])
    def test_blank_text_yields_no_chunks_rather_than_one_empty_chunk(self, text):
        result = BoundaryPreferenceChunker().chunk(text)

        assert result.chunks == []
        assert result.total_chunks == 0

    def test_text_that_fits_is_one_chunk_covering_all_of_it(self):
        text = "A short document."

        result = BoundaryPreferenceChunker(default_chunk_size=1000).chunk(text)

        assert [produced.text for produced in result.chunks] == [text]
        assert result.chunks[0].start_char == 0
        assert result.chunks[0].end_char == len(text)
        assert result.chunks[0].overlap_with_previous == 0

    def test_the_final_chunk_ends_the_split_rather_than_being_rewound_into_slivers(self):
        """A non-zero overlap must not apply to the chunk that reaches the end.

        The contributed implementation rewound unconditionally, so once a
        chunk ended at the last character `start` advanced by the one
        character the progress guard forces and the tail came out as a run of
        one-character-shorter chunks. Every character is still covered and
        the spans still reassemble, so neither of this module's two
        properties can see it -- only the count can, which is why it is
        asserted here as a count.
        """
        text = "A short document."

        result = BoundaryPreferenceChunker(default_chunk_size=1000, default_overlap=200).chunk(text)

        assert result.total_chunks == 1

    def test_a_token_longer_than_the_ceiling_is_cut_rather_than_defeating_the_bound(self):
        """One pathological run must not make every later chunk oversized."""
        text = "x" * 250

        result = BoundaryPreferenceChunker(default_chunk_size=100, default_overlap=0).chunk(text)

        assert [produced.length for produced in result.chunks] == [100, 100, 50]


class TestBoundaryQuality:
    def test_a_paragraph_break_far_back_in_the_window_is_still_used(self):
        """The first of the three differences, and the one with a number in it.

        `SlidingWindowChunker` searches the last 500 characters of the
        window. At its own default size of 3000 a paragraph break 600
        characters back is out of reach, so the cut lands mid-word instead.
        The assertion is against both chunkers on purpose: it states the
        improvement rather than merely the behaviour, so it would fail if
        this chunker were quietly replaced by the other.
        """
        text = "alpha " * 400 + "\n\n" + "beta " * 400
        break_at = len("alpha " * 400) + 2

        preferred = BoundaryPreferenceChunker(default_chunk_size=3000, default_overlap=0)
        sliding = SlidingWindowChunker(default_chunk_size=3000, default_overlap=0)

        assert preferred.chunk(text).chunks[0].end_char == break_at
        assert sliding.chunk(text).chunks[0].end_char != break_at

    def test_a_sentence_ending_before_a_quote_is_a_boundary(self):
        """The second difference: `[.!?]\\s+(?=[A-Z])` cannot see this one.

        The terminator is followed by a closing quote and then by a lowercase
        word, so the other chunker's regex declines twice over and falls
        through to a word boundary mid-clause.
        """
        text = 'He said "it was over." then he left the room and nobody followed him out.'
        after_quote = text.index("then")

        result = BoundaryPreferenceChunker(default_chunk_size=40, default_overlap=0).chunk(text)

        assert result.chunks[0].end_char == after_quote

    def test_a_paragraph_break_beats_a_later_sentence_end(self):
        """The cascade is a preference, not a search for the latest boundary."""
        text = "One.\n\nTwo. Three. Four."

        result = BoundaryPreferenceChunker(default_chunk_size=20, default_overlap=0).chunk(text)

        assert result.chunks[0].text == "One.\n\n"

    def test_a_sentence_end_beats_a_later_word_boundary(self):
        text = "One two. three four five six seven"

        result = BoundaryPreferenceChunker(default_chunk_size=20, default_overlap=0).chunk(text)

        assert result.chunks[0].text == "One two. "

    def test_a_trailing_separator_stays_on_the_chunk_it_ends(self):
        """Stripping it would make the partition lossy and shift every later offset."""
        result = BoundaryPreferenceChunker(default_chunk_size=8, default_overlap=0).chunk(
            "One.\n\nTwo."
        )

        assert result.chunks[0].text.endswith("\n\n")

    def test_crlf_survives_chunking(self):
        """Boundary detection chooses among split points; it never rewrites text."""
        text = "One.\r\n\r\nTwo. Three."

        result = BoundaryPreferenceChunker(default_chunk_size=10, default_overlap=0).chunk(text)

        assert "".join(produced.text for produced in result.chunks) == text


class TestDegenerateAdvance:
    """A boundary near the start followed by a long boundary-free stretch.

    Both of `TestBoundaryQuality`'s shapes -- regular boundaries and no
    boundaries at all -- already work; CLAUDE.md names this exact gap. The
    contributed implementation re-finds the *same* early boundary on every
    iteration once `start` has rewound past it (the boundary is still ahead
    of the new `start`, so the search returns it again), so `end` never
    advances even though `start` creeps forward one character at a time. That
    yields hundreds of near-empty chunks instead of a hard split at the
    ceiling.
    """

    def test_a_boundary_near_the_start_does_not_stall_the_rest_of_the_document(self):
        text = "y" * 1800 + "\n" + "z" * 6000

        result = BoundaryPreferenceChunker(default_chunk_size=3000, default_overlap=200).chunk(text)

        assert result.total_chunks <= 5
        assert all(produced.length >= 100 for produced in result.chunks)

    def test_the_window_end_always_advances_past_the_previous_chunks_end(self):
        """The invariant that was actually violated.

        A chunk-count assertion alone is satisfiable by an implementation
        that still produces some short chunks; this pins the mechanism the
        bug broke -- every `end_char` must strictly exceed the previous
        chunk's, so a boundary already spent cannot be reused.
        """
        text = "y" * 1800 + "\n" + "z" * 6000

        result = BoundaryPreferenceChunker(default_chunk_size=3000, default_overlap=200).chunk(text)

        for earlier, later in pairwise(result.chunks):
            assert later.end_char > earlier.end_char

    def test_dense_structured_text_with_an_early_boundary_chunks_sanely(self):
        """Shaped like the STaRK document that triggered this: long lines,
        one boundary near the start, then ~45k characters of dense text with
        no further whitespace runs long enough to register as a boundary.
        """
        text = "field: value, " * 100 + "\n\n" + "x" * 45000

        result = BoundaryPreferenceChunker(default_chunk_size=3000, default_overlap=200).chunk(text)

        assert result.total_chunks < 30
        assert all(produced.length >= 100 for produced in result.chunks)


class TestSplitting:
    def test_a_chunk_is_exactly_the_span_it_claims(self):
        text = "sentence one. sentence two. sentence three. " * 20

        result = BoundaryPreferenceChunker(default_chunk_size=200, default_overlap=20).chunk(text)

        for produced in result.chunks:
            assert produced.text == text[produced.start_char : produced.end_char]

    def test_consecutive_chunks_actually_overlap_and_report_by_how_much(self):
        text = "sentence one. sentence two. sentence three. " * 20

        result = BoundaryPreferenceChunker(default_chunk_size=200, default_overlap=40).chunk(text)

        assert len(result.chunks) > 2
        for earlier, later in pairwise(result.chunks):
            assert later.start_char < earlier.end_char
            assert later.overlap_with_previous == earlier.end_char - later.start_char

    def test_chunk_indices_are_consecutive_from_zero(self):
        result = BoundaryPreferenceChunker(default_chunk_size=100, default_overlap=10).chunk(
            "word " * 500
        )

        assert [produced.chunk_index for produced in result.chunks] == list(
            range(len(result.chunks))
        )


class TestProperties:
    @given(text=texts(), chunk_size=st.integers(min_value=1, max_value=2000))
    @settings(max_examples=100)
    def test_chunking_terminates_and_no_chunk_exceeds_its_budget(self, text, chunk_size):
        """Termination is the property; a hang reads as CI trouble, not a bug."""
        chunker = BoundaryPreferenceChunker(default_chunk_size=chunk_size, default_overlap=0)

        result = chunker.chunk(text)

        assert all(produced.length <= chunk_size for produced in result.chunks)
        assert result.total_chunks == len(result.chunks)

    @given(
        text=texts(min_size=1),
        chunk_size=st.integers(min_value=1, max_value=500),
    )
    @settings(max_examples=100)
    def test_without_overlap_the_chunks_reassemble_the_input_exactly(self, text, chunk_size):
        """The guarantee the offsets rest on, stated as reassembly rather than coverage.

        Coverage of the offsets is the weaker claim and the one
        `SlidingWindowChunker` is tested with: it cannot see a chunk whose
        `text` disagrees with the span it names. Reassembly can, and here it
        is available because a zero overlap makes the chunks a partition --
        there is no overlap arithmetic for the assertion to have to restate.
        """
        result = BoundaryPreferenceChunker(default_chunk_size=chunk_size, default_overlap=0).chunk(
            text
        )

        if not text.strip():
            assert result.chunks == []
            return
        assert "".join(produced.text for produced in result.chunks) == text

    @given(
        text=texts(min_size=1),
        chunk_size=st.integers(min_value=2, max_value=500),
        overlap=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=100)
    def test_every_character_lands_in_some_chunk_whatever_the_overlap(
        self, text, chunk_size, overlap
    ):
        result = BoundaryPreferenceChunker(
            default_chunk_size=chunk_size, default_overlap=min(overlap, chunk_size - 1)
        ).chunk(text)

        if not text.strip():
            assert result.chunks == []
            return
        covered: set[int] = set()
        for produced in result.chunks:
            covered.update(range(produced.start_char, produced.end_char))
        assert covered == set(range(len(text)))

    @given(text=texts(min_size=1))
    @settings(max_examples=60)
    def test_chunking_the_same_text_twice_gives_the_same_chunks(self, text):
        chunker = BoundaryPreferenceChunker(default_chunk_size=300, default_overlap=40)

        assert chunker.chunk(text).chunks == chunker.chunk(text).chunks

    @given(text=texts(min_size=1))
    @settings(max_examples=60)
    def test_chunks_are_ordered_and_start_at_the_beginning(self, text):
        result = BoundaryPreferenceChunker(default_chunk_size=300, default_overlap=40).chunk(text)

        if not result.chunks:
            return
        assert result.chunks[0].start_char == 0
        starts = [produced.start_char for produced in result.chunks]
        assert starts == sorted(starts)


def test_the_chunker_reports_the_type_the_protocol_asks_for():
    assert BoundaryPreferenceChunker().chunker_type == "boundary_preference"


def test_the_two_chunkers_report_different_types():
    """The type is recorded on results and is half of `index_documents`' key.

    Two chunkers sharing an identifier would make a re-index with the other
    one read as a repeat and emit nothing.
    """
    assert BoundaryPreferenceChunker().chunker_type != SlidingWindowChunker().chunker_type


def test_a_chunker_satisfies_the_protocol():
    assert isinstance(BoundaryPreferenceChunker(), Chunker)


@given(text=texts(min_size=400))
@settings(max_examples=60)
def test_no_chunk_is_wholly_contained_in_another(text: str) -> None:
    """The sibling's copy of `SlidingWindowChunker`'s property.

    `BoundaryPreferenceChunker` passes this today and passed it before the
    sliding-window fix -- checked over 400 random texts up to 9,000 characters
    while making that change. It is here anyway, because
    `.claude/rules/recurring-defects.md` §1 is about exactly this: two
    implementations of one protocol drifting because each one's tests assert
    only its own behaviour.

    A redundant chunk is invisible to every other property in both files. It
    does not break coverage, ordering, determinism or the size ceiling -- it
    is a chunk whose every character another chunk already carries, and only
    a comparison across chunks can see it.
    """
    result = BoundaryPreferenceChunker().chunk(text)

    spans = [(produced.start_char, produced.end_char) for produced in result.chunks]

    assert not [
        (inner, outer)
        for inner in spans
        for outer in spans
        if inner != outer and outer[0] <= inner[0] and inner[1] <= outer[1]
    ]
