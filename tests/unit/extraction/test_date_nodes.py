"""Dates the model filed as entities, and the dates recovered out of them.

The defect this guards is measured rather than imagined: a real 5,647-entity
corpus held 356 entities of type `temporal_expression`, none with a
description, none with properties, 335 of them isolated, and two of them
carrying dates that belonged on the entities they were related to. See
`extraction/date_nodes.py`.

Two properties are pinned hardest here, because they are the two that make
this safe to run on every extraction unconditionally:

* **No real entity is caught.** The false-positive cases below are real names
  from that corpus which `parse_temporal` accepts as dates. If the anchor
  check is ever removed, `test_a_short_real_name_is_not_a_date_node` is what
  fails, and it fails naming `Borg`.
* **A date on a date-node is not lost.** Deleting the node without lifting
  would have been a smaller diff and would have thrown away the only thing in
  it worth keeping.
"""

from __future__ import annotations

import pytest

from redstring.extraction.date_nodes import is_date_node, lift_date_nodes
from redstring.extraction.schema import ExtractedEntity, ExtractedRelationship, Extraction


def entity(name: str, entity_type: str = "concept", **kwargs: object) -> ExtractedEntity:
    return ExtractedEntity(name=name, entity_type=entity_type, **kwargs)  # type: ignore[arg-type]


def edge(source: str, target: str, kind: str = "temporal_expression") -> ExtractedRelationship:
    return ExtractedRelationship(source_name=source, target_name=target, relationship_type=kind)


# Real names from the measured corpus that `parse_temporal` reads as dates.
# Every one of them is an entity a reader would report as missing.
@pytest.mark.parametrize(
    "name", ["Borg", "Seven of Nine", "Kor", "MIT", "Sun", "API", "DIS", "Tom", "M 33", "#1", "G2"]
)
def test_a_short_real_name_is_not_a_date_node(name: str) -> None:
    """The anchor check, stated as the names that need it.

    `parse_temporal` accepts all of these. Without the year-or-month anchor
    each one is caught and deleted, and this test is the only thing standing
    between a Star Trek corpus and the loss of the Borg.
    """
    assert not is_date_node(entity(name))


@pytest.mark.parametrize(
    "name", ["the 1990s", "2017", "September 2016", "January 1968", "1966-1967", "1970"]
)
def test_a_bare_date_is_a_date_node(name: str) -> None:
    assert is_date_node(entity(name, "temporal_expression"))


@pytest.mark.parametrize(
    "name",
    [
        "early 1960s",
        "late 1967",
        "the end of the year",
        "first season",
        "December 1967 and March 1968",
        "June 28 - July 4, 1997",
    ],
)
def test_a_date_node_parse_temporal_cannot_read_is_left_alone(name: str) -> None:
    """The detector's ceiling is `parse_temporal`'s, and that is the right
    ceiling.

    These are real `temporal_expression` node names from the corpus -- 14 of
    the 357 -- and every one of them fails to parse. Catching them anyway
    would mean deleting a node while lifting nothing out of it, which trades
    visible junk for an invisible loss. They stay, and the graph-read filter
    downstream is where a reader stops seeing them.
    """
    assert not is_date_node(entity(name, "temporal_expression"))


def test_a_dated_name_with_a_description_is_kept() -> None:
    """Content is what separates a date-node from a badly named event.

    Measured: 92 of 286 `event` entities in the corpus have names that parse
    as dates, and they carry descriptions. Those are events the model named
    poorly, not dates it misfiled -- there is something in them to read, so
    they keep their node.
    """
    assert not is_date_node(
        entity("December 7, 1979", "event", description="The film premiered in Washington.")
    )
    assert not is_date_node(entity("1967", "event", properties={"outcome": "cancelled"}))


def test_a_date_node_gives_its_date_to_the_entity_it_dates() -> None:
    """The whole point. Reverting the lift leaves this red on the assertion
    about `temporal_expression`, not on the node count."""
    lifted, gained, dropped = lift_date_nodes(
        Extraction(
            entities=[
                entity("United Paramount Network", "organization"),
                entity("the 1990s", "temporal_expression"),
            ],
            relationships=[edge("United Paramount Network", "the 1990s")],
        )
    )
    assert [e.name for e in lifted.entities] == ["United Paramount Network"]
    assert lifted.entities[0].temporal_expression == "the 1990s"
    assert lifted.relationships == []
    assert (gained, dropped) == (1, 1)


def test_the_date_is_lifted_whichever_end_of_the_edge_it_sits_on() -> None:
    """The corpus holds both directions, so neither may be assumed."""
    for relationship in (edge("Star Trek", "January 1968"), edge("January 1968", "Star Trek")):
        lifted, _, _ = lift_date_nodes(
            Extraction(
                entities=[entity("Star Trek", "work"), entity("January 1968", "date")],
                relationships=[relationship],
            )
        )
        assert lifted.entities[0].temporal_expression == "January 1968"


def test_an_entity_that_stated_its_own_date_keeps_it() -> None:
    """A date-node edge is the weaker of two pieces of evidence.

    Written because the obvious implementation -- assign whenever an edge is
    found -- passes every other test in this file and silently prefers the
    model's sloppier answer to its careful one.
    """
    lifted, gained, _ = lift_date_nodes(
        Extraction(
            entities=[
                entity("Star Trek", "work", temporal_expression="September 8, 1966"),
                entity("1968", "temporal_expression"),
            ],
            relationships=[edge("Star Trek", "1968")],
        )
    )
    assert lifted.entities[0].temporal_expression == "September 8, 1966"
    assert gained == 0


def test_an_isolated_date_node_is_dropped_and_lifts_nothing() -> None:
    """335 of the 356 measured date-nodes were isolated."""
    lifted, gained, dropped = lift_date_nodes(
        Extraction(entities=[entity("Ada Lovelace", "person"), entity("2017", "date")])
    )
    assert [e.name for e in lifted.entities] == ["Ada Lovelace"]
    assert (gained, dropped) == (0, 1)


def test_one_date_node_dates_every_entity_related_to_it() -> None:
    lifted, gained, dropped = lift_date_nodes(
        Extraction(
            entities=[entity("A", "work"), entity("B", "work"), entity("1968", "date")],
            relationships=[edge("A", "1968"), edge("B", "1968")],
        )
    )
    assert {e.name: e.temporal_expression for e in lifted.entities} == {"A": "1968", "B": "1968"}
    assert (gained, dropped) == (2, 1)


def test_an_edge_between_two_date_nodes_lifts_nothing() -> None:
    """It states nothing about anything, and keeping one end would invent a
    fact about the other."""
    lifted, gained, dropped = lift_date_nodes(
        Extraction(
            entities=[entity("1967", "date"), entity("1968", "date")],
            relationships=[edge("1967", "1968")],
        )
    )
    assert lifted.entities == []
    assert lifted.relationships == []
    assert (gained, dropped) == (0, 2)


def test_an_endpoint_spelled_in_another_case_still_matches() -> None:
    """`_map_relationships` resolves endpoints through `normalize_name`
    because the model re-spells them constantly. Matching raw strings here
    would drop the lift on exactly those edges and leave the count at 0."""
    lifted, gained, _ = lift_date_nodes(
        Extraction(
            entities=[entity("Star Trek", "work"), entity("January 1968", "date")],
            relationships=[edge("star trek", "JANUARY 1968")],
        )
    )
    assert lifted.entities[0].temporal_expression == "January 1968"
    assert gained == 1


def test_a_date_node_with_a_more_precise_field_lifts_the_field() -> None:
    lifted, _, _ = lift_date_nodes(
        Extraction(
            entities=[
                entity("Star Trek", "work"),
                entity("1968", "date", temporal_expression="January 1968"),
            ],
            relationships=[edge("Star Trek", "1968")],
        )
    )
    assert lifted.entities[0].temporal_expression == "January 1968"


def test_an_extraction_with_no_date_nodes_is_returned_unchanged() -> None:
    """Identity, not merely equality: the common case must not pay for a
    rebuild of every entity, and the caller may still log the raw answer."""
    original = Extraction(
        entities=[entity("Ada Lovelace", "person")],
        relationships=[edge("Ada Lovelace", "Ada Lovelace", "knows")],
    )
    lifted, gained, dropped = lift_date_nodes(original)
    assert lifted is original
    assert (gained, dropped) == (0, 0)


def test_the_input_is_not_mutated() -> None:
    """A pass that edits the provider's answer in place makes any log of that
    answer a lie."""
    original = Extraction(
        entities=[entity("Star Trek", "work"), entity("1968", "date")],
        relationships=[edge("Star Trek", "1968")],
    )
    lift_date_nodes(original)
    assert len(original.entities) == 2
    assert original.entities[0].temporal_expression is None
    assert len(original.relationships) == 1


class TestThroughTheMapper:
    """The lift as `map_extraction` performs it, not as `lift_date_nodes` does.

    `date_nodes.py` is a pure pass over one dataclass and every test above
    drives it directly. That proves the pass works; it cannot prove the mapper
    calls it -- which is the `CLAUDE.md` "port with one adapter and no test
    between them" shape, and the reason these exist.
    """

    def test_a_date_node_never_becomes_an_entity(self) -> None:
        """Reverting the `lift_date_nodes` call in `map_extraction` leaves
        this red with two entities instead of one."""
        result = _mapped(
            Extraction(
                entities=[entity("Star Trek", "work"), entity("January 1968", "date")],
                relationships=[edge("Star Trek", "January 1968")],
            )
        )
        assert [e.name for e in result.entities] == ["Star Trek"]
        assert result.relationships == []
        assert (result.lifted_dates, result.date_nodes) == (1, 1)

    def test_the_lifted_date_reaches_the_entity_as_a_real_extent(self) -> None:
        """The lift is only worth doing if the date survives parsing.

        `lift_date_nodes` writes a string into `temporal_expression`;
        `_build_entity` is what turns that into a `TemporalExtent`. A lift
        that produced a string nothing could parse would satisfy every test
        above and still leave the timeline empty -- which is the defect this
        whole change is about, one layer further down.
        """
        result = _mapped(
            Extraction(
                entities=[entity("Star Trek", "work"), entity("January 1968", "date")],
                relationships=[edge("Star Trek", "January 1968")],
            )
        )
        assert result.entities[0].temporal is not None
        assert result.entities[0].temporal.start_date is not None
        assert result.entities[0].temporal.start_date.year == 1968
        assert result.entities[0].temporal.original_text == "January 1968"

    def test_an_edge_to_a_date_node_is_not_counted_as_unresolved(self) -> None:
        """It is removed, not dropped, and the two counters mean different
        things: `unresolved_relationships` says the prompt is not landing,
        while `date_nodes` says the model is misfiling dates. Folding one into
        the other would hide both.

        **This test passes with the lift reverted**, and is kept anyway. With
        no lift the date-node is a real entity, so the edge resolves and is
        not unresolved either -- the counter is 0 for the opposite reason.
        What it guards is a *future* implementation that removes date-nodes
        without removing their edges, which is the obvious simplification of
        this pass and which would send every such edge to the unresolved
        count, where it would read as a prompt failure.
        """
        result = _mapped(
            Extraction(
                entities=[entity("Star Trek", "work"), entity("January 1968", "date")],
                relationships=[edge("Star Trek", "January 1968")],
            )
        )
        assert result.unresolved_relationships == 0


def _mapped(extraction: Extraction):
    from datetime import UTC, datetime
    from uuid import UUID

    from redstring.extraction.mapping import map_extraction

    return map_extraction(
        extraction,
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        source_id="doc-1",
        model="test-model",
        reference_date=None,
        observed_at=datetime(2026, 2, 4, 11, 7, tzinfo=UTC),
    )
