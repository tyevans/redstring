"""Named maxima on chunk size, overlap and gleaning passes.

Each maximum is tested at its own value (accepted) and one above it (refused).
The chunker refusals raise `ChunkSizeError`, which is a `RedstringError` and
not a `ValueError`: that is the existing chunker contract, and the tests pin
it rather than changing it.
"""

from __future__ import annotations

import pytest

from redstring.extraction.chunkers import SlidingWindowChunker
from redstring.extraction.chunkers.boundary_preference_chunker import (
    BoundaryPreferenceChunker,
)
from redstring.extraction.chunkers.limits import MAX_CHUNK_SIZE, MAX_OVERLAP_SIZE
from redstring.extraction.errors import ChunkSizeError
from redstring.extraction.gleaning import MAX_GLEANINGS, check_gleanings
from redstring.extraction.pipeline import ExtractionPipeline
from tests.unit.extraction.test_pipeline_concurrency import RecordingProvider

SHORT_TEXT = "Ada Lovelace wrote notes on the engine."


class TestChunkSizeMaxima:
    @pytest.mark.parametrize("chunker_type", [SlidingWindowChunker, BoundaryPreferenceChunker])
    def test_the_maximum_chunk_size_is_accepted_at_construction(self, chunker_type):
        chunker_type(default_chunk_size=MAX_CHUNK_SIZE, default_overlap=0)

    @pytest.mark.parametrize("chunker_type", [SlidingWindowChunker, BoundaryPreferenceChunker])
    def test_one_above_the_maximum_chunk_size_is_refused_at_construction(self, chunker_type):
        with pytest.raises(ChunkSizeError, match="must be <="):
            chunker_type(default_chunk_size=MAX_CHUNK_SIZE + 1, default_overlap=0)

    @pytest.mark.parametrize("chunker_type", [SlidingWindowChunker, BoundaryPreferenceChunker])
    def test_the_maximum_chunk_size_is_accepted_per_call(self, chunker_type):
        chunks = chunker_type().chunk(SHORT_TEXT, max_chunk_size=MAX_CHUNK_SIZE).chunks
        assert [c.text for c in chunks] == [SHORT_TEXT]

    @pytest.mark.parametrize("chunker_type", [SlidingWindowChunker, BoundaryPreferenceChunker])
    def test_one_above_the_maximum_chunk_size_is_refused_per_call(self, chunker_type):
        with pytest.raises(ChunkSizeError, match="must be <="):
            chunker_type().chunk(SHORT_TEXT, max_chunk_size=MAX_CHUNK_SIZE + 1)


class TestOverlapMaxima:
    @pytest.mark.parametrize("chunker_type", [SlidingWindowChunker, BoundaryPreferenceChunker])
    def test_the_maximum_overlap_is_accepted_at_construction(self, chunker_type):
        chunker_type(default_chunk_size=MAX_CHUNK_SIZE, default_overlap=MAX_OVERLAP_SIZE)

    @pytest.mark.parametrize("chunker_type", [SlidingWindowChunker, BoundaryPreferenceChunker])
    def test_one_above_the_maximum_overlap_is_refused_at_construction(self, chunker_type):
        with pytest.raises(ChunkSizeError, match="must be <="):
            chunker_type(default_chunk_size=MAX_CHUNK_SIZE, default_overlap=MAX_OVERLAP_SIZE + 1)

    @pytest.mark.parametrize("chunker_type", [SlidingWindowChunker, BoundaryPreferenceChunker])
    def test_the_maximum_overlap_is_accepted_per_call(self, chunker_type):
        chunks = chunker_type().chunk(
            SHORT_TEXT, max_chunk_size=MAX_CHUNK_SIZE, overlap_size=MAX_OVERLAP_SIZE
        )
        assert [c.text for c in chunks.chunks] == [SHORT_TEXT]

    @pytest.mark.parametrize("chunker_type", [SlidingWindowChunker, BoundaryPreferenceChunker])
    def test_one_above_the_maximum_overlap_is_refused_per_call(self, chunker_type):
        with pytest.raises(ChunkSizeError, match="must be <="):
            chunker_type().chunk(
                SHORT_TEXT, max_chunk_size=MAX_CHUNK_SIZE, overlap_size=MAX_OVERLAP_SIZE + 1
            )


class TestRuntimeOverridesFollowInitValidation:
    """`chunk()` applies the same rules as `__init__`, not a subset of them."""

    def test_a_per_call_overlap_not_below_the_chunk_size_is_refused(self):
        with pytest.raises(ChunkSizeError, match="must be <"):
            SlidingWindowChunker().chunk(SHORT_TEXT, max_chunk_size=200, overlap_size=200)

    def test_a_per_call_overlap_one_below_the_chunk_size_is_accepted(self):
        result = SlidingWindowChunker().chunk(SHORT_TEXT, max_chunk_size=200, overlap_size=199)
        assert result.chunks

    def test_a_per_call_chunk_below_the_constructed_minimum_is_refused(self):
        chunker = SlidingWindowChunker(min_chunk_size=100)
        with pytest.raises(ChunkSizeError, match="min_chunk_size"):
            chunker.chunk("x" * 500, max_chunk_size=50, overlap_size=0)

    def test_a_per_call_overlap_of_zero_is_refused_when_negative(self):
        with pytest.raises(ChunkSizeError, match="must be >= 0"):
            SlidingWindowChunker().chunk(SHORT_TEXT, overlap_size=-1)

    def test_the_absent_override_still_means_the_default(self):
        chunker = SlidingWindowChunker(default_chunk_size=3000, default_overlap=200)
        assert chunker.chunk(SHORT_TEXT).chunks


class TestGleaningMaximum:
    def test_the_maximum_gleaning_count_is_accepted(self):
        check_gleanings(MAX_GLEANINGS)

    def test_one_above_the_maximum_gleaning_count_is_refused(self):
        with pytest.raises(ValueError, match="gleanings must be <="):
            check_gleanings(MAX_GLEANINGS + 1)

    def test_a_negative_gleaning_count_is_refused(self):
        with pytest.raises(ValueError, match="gleanings must be >= 0"):
            check_gleanings(-1)

    def test_the_maximum_is_accepted_by_the_pipeline(self):
        ExtractionPipeline(RecordingProvider(), gleanings=MAX_GLEANINGS)

    def test_one_above_the_maximum_is_refused_by_the_pipeline_before_any_call(self):
        provider = RecordingProvider()
        with pytest.raises(ValueError, match="gleanings must be <="):
            ExtractionPipeline(provider, gleanings=MAX_GLEANINGS + 1)
        assert provider.prompts == []
