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
from itertools import pairwise
from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st

from redstring.extraction.mapping import (
    map_extraction,
    preference,
    relationship_preference,
)
from redstring.extraction.merging import merge_extractions
from redstring.extraction.schema import (
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
        reference_date=None,
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

    def test_a_tie_on_edge_confidence_is_broken_without_letting_chunk_order_decide(self):
        """Two overlapping chunks reporting one edge, one of them with evidence.

        `_relationship_preference` was `(confidence, relationship_type)`, and
        the type is already fixed by the id -- it is one of the three inputs
        to `_relationship_id_for` -- so the tuple degenerated to
        `(confidence,)` and the strict `>` fell through to "keep what's
        there". Which `properties` survived was decided by chunk arrival
        order, so the same document extracted twice produced different
        `DocumentExtracted` payloads in a durable, replayable log.
        """
        pair = (entity("Ada Lovelace"), entity("Charles Babbage"))
        evidenced = chunk(
            *pair,
            links=[
                ExtractedRelationship(
                    source_name="Ada Lovelace",
                    target_name="Charles Babbage",
                    relationship_type="KNOWS",
                    properties={"evidence": "letters"},
                )
            ],
        )
        bare = chunk(*pair, links=[link("Ada Lovelace", "Charles Babbage", "KNOWS")])

        forwards = merge_extractions([evidenced, bare]).relationships
        backwards = merge_extractions([bare, evidenced]).relationships

        assert [edge.properties for edge in forwards] == [{"evidence": "letters"}]
        assert [edge.properties for edge in backwards] == [{"evidence": "letters"}]

    def test_within_one_answer_and_across_chunks_pick_the_same_edge(self):
        """The consistency claim, asserted rather than assumed.

        One `map_extraction` seeing both statements and two chunks seeing one
        each must agree about which survives. They used different rules --
        `setdefault` versus `_relationship_preference` -- so they did not.
        """
        pair = (entity("Ada Lovelace"), entity("Charles Babbage"))
        hedged = link("Ada Lovelace", "Charles Babbage", "KNOWS", confidence=0.2)
        certain = link("Ada Lovelace", "Charles Babbage", "KNOWS", confidence=0.9)

        [within] = chunk(*pair, links=[hedged, certain]).relationships
        [across] = merge_extractions(
            [chunk(*pair, links=[hedged]), chunk(*pair, links=[certain])]
        ).relationships

        assert within.confidence == across.confidence

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


NAMES = st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=6)

#: Distinct *content* under an identical identity key.
#:
#: This is what makes the properties below able to fail. `entity_id_for`
#: reads only the name and type, so two mentions with the same name and
#: different descriptions are one entity that the tie-break must choose
#: between -- whereas mentions built from a bare name alone are fully **equal
#: objects**, and "first wins" and "last wins" then return the same result for
#: any implementation at all. That vacuity is exactly what let the
#: relationship tie-break defect through review (fix round 1, Important 1).
#:
#: Confidence is deliberately left at `DEFAULT_CONFIDENCE` throughout: a tie
#: on confidence is the case the order has to resolve on its later fields, and
#: it is the realistic case, since an unscored mention is what a model
#: usually returns.
DESCRIPTIONS = st.one_of(st.none(), st.text(min_size=0, max_size=12))

MENTIONS = st.lists(st.tuples(NAMES, DESCRIPTIONS), min_size=1, max_size=6)


def mentions_of(pairs) -> list[ExtractedEntity]:
    return [entity(name, description=description) for name, description in pairs]


def edges_among(pairs) -> list[ExtractedRelationship]:
    """Every consecutive pair, so a chunk's edges reference its own entities.

    An edge naming an entity the same chunk did not list is unresolvable and
    would be dropped before the merge ever sees it, which would make the
    relationship half of every property below vacuous in a second way.
    """
    names = [name for name, _ in pairs]
    return [link(earlier, later, "KNOWS") for earlier, later in pairwise(names) if earlier != later]


def part_from(pairs):
    return chunk(*mentions_of(pairs), links=edges_among(pairs))


#: Every property below builds its input through `map_extraction`, which
#: calls `parse_temporal`, whose last strategy imports `dateparser` -- ~250ms
#: on the first call in a process (BACKLOG B50). Whichever property an xdist
#: worker happens to draw first pays it, and at hypothesis's 200ms default it
#: fails as `DeadlineExceeded` in whichever file that was. Observed here on
#: `test_merging_the_same_chunk_twice_equals_merging_it_once` at 299ms during
#: slice 9, in a run that changed nothing in this file.
#:
#: An upper bound on a one-off library import is not a property of
#: `merge_extractions`, so the deadline is dropped rather than raised. That
#: reasoning generalised: deadlines are now off for the whole suite, decided
#: once in `tests/conftest.py`, and the observation recorded here is part of
#: why.


def by_id(items):
    return sorted(items, key=lambda item: item.id)


class TestProperties:
    @given(chunks=st.lists(MENTIONS, min_size=1, max_size=5), seed=st.integers())
    def test_permuting_the_chunks_changes_nothing(self, chunks, seed):
        """Order-independence, over duplicates tied on confidence but not content.

        The version of this property that shipped in the slice drew entities
        from a bare name, which made duplicates fully *equal objects* -- and
        "first wins" and "last wins" agree on equal objects, so the property
        held for a partial tie-break as readily as a total one. It is the
        CLAUDE.md failure shape one level up from the one it was written to
        catch, and it is why Important 1 survived review.

        `DESCRIPTIONS` supplies the content that differs; the confidence tie
        stays, because that is the case the later fields of the order exist
        to resolve.
        """
        parts = [part_from(group) for group in chunks]
        shuffled = list(parts)
        random.Random(seed).shuffle(shuffled)

        forwards = merge_extractions(parts)
        backwards = merge_extractions(shuffled)

        assert by_id(forwards.entities) == by_id(backwards.entities)
        assert by_id(forwards.relationships) == by_id(backwards.relationships)

    @given(groups=st.lists(MENTIONS, min_size=1, max_size=4))
    def test_merging_the_same_chunk_twice_equals_merging_it_once(self, groups):
        """Idempotence, over freshly built parts rather than the same object twice.

        `merge([x, x]) == merge([x])` is satisfied by an implementation that
        compares identity, so the duplicate is a separate `MappedExtraction`
        built from an equal payload -- which is what a re-delivered chunk
        actually is.
        """
        once = [part_from(group) for group in groups]
        twice = once + [part_from(group) for group in groups]

        assert by_id(merge_extractions(once).entities) == by_id(merge_extractions(twice).entities)
        assert by_id(merge_extractions(once).relationships) == by_id(
            merge_extractions(twice).relationships
        )

    @given(groups=st.lists(MENTIONS, min_size=1, max_size=4))
    def test_merging_an_already_merged_result_is_a_no_op(self, groups):
        """The other idempotence, and the one a fold has to have.

        Distinct from the test above: that one says duplicate *inputs* do not
        multiply, this one says the operation is stable under reapplication,
        which is what makes chunk-at-a-time folding equal all-at-once.
        """
        parts = [part_from(group) for group in groups]
        merged = merge_extractions(parts)

        assert merge_extractions([merged]) == merged

    @given(groups=st.lists(MENTIONS, min_size=2, max_size=5))
    def test_folding_pairwise_equals_merging_everything_at_once(self, groups):
        """Associativity. A streaming caller must get the same answer as a batch one."""
        parts = [part_from(group) for group in groups]

        one_shot = merge_extractions(parts)
        folded = parts[0]
        for part in parts[1:]:
            folded = merge_extractions([folded, part])

        assert by_id(one_shot.entities) == by_id(folded.entities)
        assert by_id(one_shot.relationships) == by_id(folded.relationships)

    @given(mentions=MENTIONS)
    def test_two_mentions_of_one_entity_with_equal_preference_are_equal(self, mentions):
        """The claim "the order is total", stated as a property instead of a belief.

        It is also what makes the two `>` -> `>=` mutants in
        `merge_extractions` equivalent rather than uncaught. `>` keeps the
        incumbent on a tie and `>=` takes the challenger, so they differ
        **only** when two distinct objects compare equal -- precisely what
        totality forbids. Without this property those survivors would be
        indistinguishable from the partial-order defect the review found, and
        that defect already survived once by looking like an equivalent
        mutant.

        Each mention is mapped **alone**, then grouped. Mapping them together
        would be vacuous: `map_extraction` deduplicates, so its output holds
        one entity per id and every group would be a singleton -- which is
        how the first version of this test passed while the order was still
        partial.

        Scoped to what `map_extraction` produces, which is the claim being
        made: it fixes tenant, source, type, method and model across a bucket
        and never populates `external_ids`, `source_text`, `temporal` or
        `blocking_keys`, leaving confidence, name, description and properties
        -- all four of which are in the key.
        """
        grouped: dict[tuple, list] = {}
        for name, description in mentions:
            [produced] = chunk(entity(name, description=description)).entities
            grouped.setdefault((produced.id, preference(produced)), []).append(produced)

        for tied in grouped.values():
            assert all(other == tied[0] for other in tied)

    @given(
        statements=st.lists(
            st.tuples(st.floats(0.0, 1.0), st.dictionaries(NAMES, NAMES, max_size=3)),
            min_size=1,
            max_size=6,
        )
    )
    def test_two_statements_of_one_edge_with_equal_preference_are_equal(self, statements):
        """The same claim for relationships, where the argument is shorter.

        The endpoints and the type are all inputs to `_relationship_id_for`,
        so within one id bucket only `confidence` and `properties` can differ
        -- and both are generated here, and both are in the key.
        """
        pair = (entity("Ada Lovelace"), entity("Charles Babbage"))
        grouped: dict[tuple, list] = {}
        for confidence, properties in statements:
            [edge] = chunk(
                *pair,
                links=[
                    ExtractedRelationship(
                        source_name="Ada Lovelace",
                        target_name="Charles Babbage",
                        relationship_type="KNOWS",
                        confidence=confidence,
                        properties=properties,
                    )
                ],
            ).relationships
            grouped.setdefault((edge.id, relationship_preference(edge)), []).append(edge)

        for tied in grouped.values():
            assert all(other == tied[0] for other in tied)

    @given(names_=st.lists(NAMES, min_size=1, max_size=6))
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
