"""`stored_chunks`: what survives the trip from a `Chunk` to a `StoredChunk`.

The pass-through of `Chunk.metadata` is the reason this module exists.
`StoredChunk.metadata` is documented as the extension point, and
`index_documents` -- the only path into the chunk corpus -- reaches it through
this function, so a caller whose chunker records something about a passage had
no way to get it stored. A downstream project hit exactly that: it needed to
carry the number of characters of synthetic header its chunker had prepended,
so retrieval could subtract them back off, and had to choose between putting
the header in the stored text with nothing to subtract or not chunking that
way at all.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from redstring.domain.chunk import chunk_id
from redstring.domain.ids import EntityId, SourceId, TenantId
from redstring.extraction.chunking import Chunk, ChunkingResult
from redstring.extraction.corpus import stored_chunks

TENANT_ID: TenantId = uuid4()
SOURCE_ID: SourceId = "doc-1"


def result(*chunks: Chunk) -> ChunkingResult:
    return ChunkingResult(
        chunks=list(chunks),
        total_chunks=len(chunks),
        original_length=sum(len(chunk.text) for chunk in chunks),
    )


def chunk(
    text: str,
    index: int,
    *,
    metadata: dict[str, object] | None = None,
) -> Chunk:
    start = index * 100
    return Chunk(
        text=text,
        chunk_index=index,
        start_char=start,
        end_char=start + len(text),
        metadata=dict(metadata) if metadata is not None else {},
    )


class TestMetadataReachesTheStore:
    def test_each_passage_keeps_its_own_metadata(self) -> None:
        """Distinct values per chunk, so one dict shared by all would fail.

        Both chunks carry the *same key*: an implementation that passed the
        first chunk's metadata to every `StoredChunk` -- or the last one's --
        would still produce two dicts with a `synthetic_prefix_chars` entry,
        and only differing values catch it.
        """
        passages = stored_chunks(
            result(
                chunk("first passage", 0, metadata={"synthetic_prefix_chars": 12}),
                chunk("second passage", 1, metadata={"synthetic_prefix_chars": 34}),
            ),
            tenant_id=TENANT_ID,
            source_id=SOURCE_ID,
        )

        assert [passage.metadata for passage in passages] == [
            {"synthetic_prefix_chars": 12},
            {"synthetic_prefix_chars": 34},
        ]

    def test_a_chunk_with_no_metadata_stores_an_empty_mapping(self) -> None:
        (passage,) = stored_chunks(
            result(chunk("lonely", 0)), tenant_id=TENANT_ID, source_id=SOURCE_ID
        )

        assert passage.metadata == {}

    def test_metadata_survives_alongside_entity_links(self) -> None:
        """The extraction path fills both; neither may cost the other."""
        entity_id = EntityId(uuid4())

        (passage,) = stored_chunks(
            result(chunk("Ada Lovelace", 0, metadata={"section": "intro"})),
            tenant_id=TENANT_ID,
            source_id=SOURCE_ID,
            entity_ids_by_index={0: [entity_id]},
        )

        assert passage.metadata == {"section": "intro"}
        assert passage.entity_ids == [entity_id]


class TestNothingIsShared:
    def test_editing_a_stored_passage_does_not_reach_the_chunker(self) -> None:
        """A `StoredChunk`'s `metadata` is mutable by design.

        If it were the chunker's own dict, a projection or a caller editing a
        stored passage would reach back into the `ChunkingResult` -- and in
        `index_documents` that result is still live while the next passages
        are built.
        """
        source = chunk("first passage", 0, metadata={"section": "intro"})

        (passage,) = stored_chunks(result(source), tenant_id=TENANT_ID, source_id=SOURCE_ID)
        passage.metadata["section"] = "tampered"

        assert source.metadata == {"section": "intro"}

    def test_editing_the_chunker_does_not_reach_a_stored_passage(self) -> None:
        source = chunk("first passage", 0, metadata={"section": "intro"})

        (passage,) = stored_chunks(result(source), tenant_id=TENANT_ID, source_id=SOURCE_ID)
        source.metadata["section"] = "moved on"

        assert passage.metadata == {"section": "intro"}

    def test_a_nested_value_is_not_shared_either(self) -> None:
        """`metadata` is `dict[str, Any]`, so nesting is where sharing hides.

        Pydantic rebuilds the outer dict while validating the field, which
        makes the two tests above pass on a bare `metadata=chunk.metadata`.
        It does not descend into an `Any`, so without an explicit copy this
        one dict is the same object on both sides -- and a caller recording
        anything structured about a passage gets exactly that shape.
        """
        source = chunk("first passage", 0, metadata={"offsets": {"prefix": 12}})

        (passage,) = stored_chunks(result(source), tenant_id=TENANT_ID, source_id=SOURCE_ID)
        source.metadata["offsets"]["prefix"] = 99

        assert passage.metadata == {"offsets": {"prefix": 12}}


class TestRepeatedPassages:
    def test_the_first_occurrence_of_a_repeated_passage_supplies_the_metadata(
        self,
    ) -> None:
        """First-seen wins, as it already does for the offsets.

        The two occurrences carry *different values for the same key*, so
        "first wins", "last wins" and "merge the two dicts" all disagree here.
        Merging is the one worth naming: it would produce a record whose
        metadata described an occurrence whose offsets were discarded.
        """
        passages = stored_chunks(
            result(
                chunk("repeated", 0, metadata={"synthetic_prefix_chars": 12}),
                chunk("distinct", 1, metadata={"synthetic_prefix_chars": 99}),
                chunk("repeated", 2, metadata={"synthetic_prefix_chars": 34}),
            ),
            tenant_id=TENANT_ID,
            source_id=SOURCE_ID,
        )

        assert len(passages) == 2
        repeated = next(
            passage for passage in passages if passage.id == chunk_id(SOURCE_ID, "repeated")
        )
        assert repeated.metadata == {"synthetic_prefix_chars": 12}
        assert repeated.start_char == 0

    def test_a_later_occurrence_holding_a_key_the_first_lacks_is_still_dropped(
        self,
    ) -> None:
        """The half of first-seen-wins a merging implementation would pass."""
        passages = stored_chunks(
            result(
                chunk("repeated", 0, metadata={"section": "intro"}),
                chunk("repeated", 1, metadata={"synthetic_prefix_chars": 34}),
            ),
            tenant_id=TENANT_ID,
            source_id=SOURCE_ID,
        )

        assert [passage.metadata for passage in passages] == [{"section": "intro"}]


class TestUnstorableMetadataIsRefused:
    def test_metadata_that_cannot_be_stored_raises_rather_than_being_written(
        self,
    ) -> None:
        """`StoredChunk` validates it; `stored_chunks` must not route around."""
        with pytest.raises(ValueError, match="metadata"):
            stored_chunks(
                result(chunk("first passage", 0, metadata={"note": "a\x00b"})),
                tenant_id=TENANT_ID,
                source_id=SOURCE_ID,
            )
