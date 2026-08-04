"""Folding per-chunk results into one, and proving the fold does not care about order.

The two properties the brief names are here as hypothesis properties rather
than examples, and both are stated over inputs chosen so a weaker
implementation would fail them:

- **idempotence**: merging the same chunk twice equals merging it once. Stated
  over a *fresh* mapping each time rather than the same object twice, because
  `merge([x, x]) == merge([x])` is true even for an implementation that
  compares by `is`.
- **order-independence**: permuting the chunks changes nothing. Stated over
  chunks whose entities are deliberately **tied** on confidence, because with
  distinct confidences the property holds for a merge whose tie-break is
  "keep the first" -- which is order-dependent exactly where it matters.
"""

from __future__ import annotations

import random
from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st

from kg_builder.extraction.mapping import map_extraction
from kg_builder.extraction.merging import merge_extractions
from kg_builder.extraction.schema import (
    DEFAULT_CONFIDENCE,
    ExtractedEntity,
    ExtractedRelationship,
    Extraction,
)

TENANT = UUID("11111111-1111-1111-1111-111111111111")
SOURCE = "doc-1"
MODEL = "fake/canned-v1"


def chunk(*entities: ExtractedEntity, links: list[ExtractedRelationship] | None = None):
    return map_extraction(
        Extraction(entities=list(entities), relationships=list(links or [])),
        tenant_id=TENANT,
        source_id=SOURCE,
        model=MODEL,
    )


def entity(name: str, entity_type: str = "Person", **kwargs) -> ExtractedEntity:
    return ExtractedEntity(name=name, entity_type=entity_type, **kwargs)


def link(source: str, target: str, kind: str = "KNOWS", **kwargs) -> ExtractedRelationship:
    return ExtractedRelationship(
        source_name=source, target_name=target, relationship_type=kind, **kwargs
    )


def names(result) -> list[str]:
    return sorted(e.name for e in result.entities)


class TestTheOverlapChunkingCreates:
    def test_one_entity_reported_by_two_chunks_is_one_entity(self):
        """The whole reason this module exists.

        Overlapping windows mean an entity near a boundary is reported twice.
        Both reports get one id from `entity_id_for`, so they were never two
        entities to begin with.
        """
        merged = merge_extractions([chunk(entity("Ada Lovelace")), chunk(entity("Ada Lovelace"))])

        assert names(merged) == ["Ada Lovelace"]

    def test_different_entities_from_different_chunks_all_survive(self):
        merged = merge_extractions(
            [chunk(entity("Ada Lovelace")), chunk(entity("Charles Babbage"))]
        )

        assert names(merged) == ["Ada Lovelace", "Charles Babbage"]

    def test_no_chunks_at_all_merges_to_nothing(self):
        merged = merge_extractions([])

        assert (merged.entities, merged.relationships) == ([], [])

    def test_relationships_are_deduplicated_the_same_way(self):
        pair = (entity("Ada Lovelace"), entity("Charles Babbage"))
        both = [
            chunk(*pair, links=[link("Ada Lovelace", "Charles Babbage")]),
            chunk(*pair, links=[link("Ada Lovelace", "Charles Babbage")]),
        ]

        assert len(merge_extractions(both).relationships) == 1

    def test_every_surviving_edge_still_has_both_endpoints_present(self):
        """`DocumentExtracted` feeds a projection that raises on a missing endpoint.

        Deduplication removes copies, never entities, so this holds by
        construction -- but it is the invariant the event depends on, and an
        implementation that deduplicated entities by *name* while keying
        edges by id would break it silently.
        """
        merged = merge_extractions(
            [
                chunk(
                    entity("Ada Lovelace"),
                    entity("Charles Babbage"),
                    links=[link("Ada Lovelace", "Charles Babbage")],
                ),
                chunk(
                    entity("Charles Babbage"),
                    entity("Analytical Engine", "Machine"),
                    links=[link("Charles Babbage", "Analytical Engine", "BUILT")],
                ),
            ]
        )
        present = {e.id for e in merged.entities}

        for edge in merged.relationships:
            assert edge.source_entity_id in present
            assert edge.target_entity_id in present


class TestChoosingBetweenTwoReports:
    def test_the_more_confident_report_wins(self):
        merged = merge_extractions(
            [
                chunk(entity("Ada Lovelace", confidence=0.2, description="glimpsed")),
                chunk(entity("Ada Lovelace", confidence=0.9, description="described")),
            ]
        )

        assert [e.description for e in merged.entities] == ["described"]

    def test_it_still_wins_when_it_comes_second(self):
        merged = merge_extractions(
            [
                chunk(entity("Ada Lovelace", confidence=0.9, description="described")),
                chunk(entity("Ada Lovelace", confidence=0.2, description="glimpsed")),
            ]
        )

        assert [e.description for e in merged.entities] == ["described"]

    def test_at_equal_confidence_the_fuller_description_wins(self):
        """Equal confidence is the common case, not the edge case.

        Every entity the model declined to score carries
        `DEFAULT_CONFIDENCE`, so a merge that broke ties by arrival order
        would be order-dependent for most real documents.
        """
        merged = merge_extractions(
            [
                chunk(entity("Ada Lovelace", description="A mathematician.")),
                chunk(entity("Ada Lovelace", description="A.")),
            ]
        )

        assert [e.description for e in merged.entities] == ["A mathematician."]

    def test_a_described_report_beats_an_undescribed_one_at_equal_confidence(self):
        merged = merge_extractions(
            [
                chunk(entity("Ada Lovelace")),
                chunk(entity("Ada Lovelace", description="A mathematician.")),
            ]
        )

        assert [e.description for e in merged.entities] == ["A mathematician."]

    def test_confidence_outranks_description_length(self):
        """Otherwise a verbose low-confidence guess would beat a terse certainty."""
        merged = merge_extractions(
            [
                chunk(entity("Ada Lovelace", confidence=0.95, description="Short.")),
                chunk(entity("Ada Lovelace", confidence=0.1, description="A very long guess " * 5)),
            ]
        )

        assert [e.confidence for e in merged.entities] == [0.95]


class TestCounters:
    def test_rows_dropped_in_any_chunk_are_all_counted(self):
        """A chunk's bad rows do not stop being bad because another chunk was fine."""
        merged = merge_extractions(
            [
                chunk(entity("  "), entity("Ada Lovelace")),
                chunk(entity(""), entity("Charles Babbage")),
            ]
        )

        assert merged.dropped_entities == 2

    def test_unresolved_edges_and_self_loops_are_counted_separately_and_summed(self):
        merged = merge_extractions(
            [
                chunk(entity("Ada Lovelace"), links=[link("Ada Lovelace", "Nobody")]),
                chunk(entity("Ada Lovelace"), links=[link("Ada Lovelace", "ada lovelace")]),
            ]
        )

        assert (merged.unresolved_relationships, merged.self_loops) == (1, 1)


TIED = st.lists(
    st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=6),
    min_size=1,
    max_size=6,
)


class TestProperties:
    @given(chunks=st.lists(TIED, min_size=1, max_size=5), seed=st.integers())
    def test_permuting_the_chunks_changes_nothing(self, chunks, seed):
        """Order-independence, over entities that are *tied* on every field.

        With distinct confidences this property holds even for a merge whose
        tie-break is "keep whichever arrived first", because no tie ever
        arises. Ties are what make it a real test -- and they are also the
        realistic case, since `DEFAULT_CONFIDENCE` is what an unscored entity
        gets.
        """
        parts = [chunk(*[entity(name) for name in group]) for group in chunks]
        shuffled = list(parts)
        random.Random(seed).shuffle(shuffled)

        forwards = merge_extractions(parts)
        backwards = merge_extractions(shuffled)

        assert sorted(forwards.entities, key=lambda e: e.id) == sorted(
            backwards.entities, key=lambda e: e.id
        )

    @given(groups=st.lists(TIED, min_size=1, max_size=4))
    def test_merging_the_same_chunk_twice_equals_merging_it_once(self, groups):
        """Idempotence, over freshly built parts rather than the same object twice.

        `merge([x, x]) == merge([x])` is satisfied by an implementation that
        compares identity, so the duplicate is a separate `MappedExtraction`
        built from an equal payload -- which is what a re-delivered chunk
        actually is.
        """
        once = [chunk(*[entity(name) for name in group]) for group in groups]
        twice = once + [chunk(*[entity(name) for name in group]) for group in groups]

        assert sorted(merge_extractions(once).entities, key=lambda e: e.id) == sorted(
            merge_extractions(twice).entities, key=lambda e: e.id
        )

    @given(groups=st.lists(TIED, min_size=1, max_size=4))
    def test_merging_an_already_merged_result_is_a_no_op(self, groups):
        """The other idempotence, and the one a fold has to have.

        Distinct from the test above: that one says duplicate *inputs* do not
        multiply, this one says the operation is stable under reapplication,
        which is what makes chunk-at-a-time folding equal all-at-once.
        """
        parts = [chunk(*[entity(name) for name in group]) for group in groups]
        merged = merge_extractions(parts)

        assert merge_extractions([merged]) == merged

    @given(groups=st.lists(TIED, min_size=2, max_size=5))
    def test_folding_pairwise_equals_merging_everything_at_once(self, groups):
        """Associativity. A streaming caller must get the same answer as a batch one."""
        parts = [chunk(*[entity(name) for name in group]) for group in groups]

        one_shot = merge_extractions(parts)
        folded = parts[0]
        for part in parts[1:]:
            folded = merge_extractions([folded, part])

        assert sorted(one_shot.entities, key=lambda e: e.id) == sorted(
            folded.entities, key=lambda e: e.id
        )

    @given(names_=TIED)
    def test_every_entity_that_went_in_comes_out(self, names_):
        """Deduplication must never lose an id, only copies of one."""
        parts = [chunk(entity(name)) for name in names_]
        went_in = {e.id for part in parts for e in part.entities}

        assert {e.id for e in merge_extractions(parts).entities} == went_in


def test_the_tie_break_is_reached_at_all_in_the_realistic_case():
    """Guards the properties above: they would be vacuous if ties never occurred.

    Two chunks reporting one entity with no stated confidence is the ordinary
    outcome of chunk overlap, and it is exactly a tie on `DEFAULT_CONFIDENCE`.
    """
    parts = [chunk(entity("Ada Lovelace")), chunk(entity("Ada Lovelace"))]

    assert {e.confidence for part in parts for e in part.entities} == {DEFAULT_CONFIDENCE}
    assert len(merge_extractions(parts).entities) == 1
