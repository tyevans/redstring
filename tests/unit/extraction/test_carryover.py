"""What a chunk carries to the next one.

Entities are built through `map_extraction` rather than by constructing
`Entity` directly, so the names and types under test are the ones the real
path produces -- CLAUDE.md's factory row in reverse: a helper that bypassed
mapping would let this module agree with a mapping that normalizes names
differently than it does.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from redstring.extraction.carryover import (
    DEFAULT_CARRYOVER_ENTITIES,
    Carryover,
)
from redstring.extraction.mapping import map_extraction
from redstring.extraction.schema import ExtractedEntity, Extraction

TENANT = UUID("11111111-1111-1111-1111-111111111111")
SOURCE = "doc-1"

#: A fixed observation instant. Never `datetime.now(UTC)`: a fixture that
#: varies per run makes any comparison on `observed_at` non-deterministic.
OBSERVED = datetime(2026, 2, 10, 11, 7, tzinfo=UTC)


def found(*pairs: tuple[str, str]):
    """Domain entities for `(name, type)` pairs, through the real mapper."""
    return map_extraction(
        Extraction(entities=[ExtractedEntity(name=name, entity_type=kind) for name, kind in pairs]),
        tenant_id=TENANT,
        source_id=SOURCE,
        model="fake/canned-v1",
        reference_date=None,
        observed_at=OBSERVED,
    ).entities


class TestWhenThereIsNothingToCarry:
    def test_a_fresh_carryover_contributes_no_block(self):
        assert Carryover().block() == ""

    def test_a_zero_limit_contributes_no_block_even_after_remembering(self):
        """ "Off" has to be off *after* the pipeline has seen a chunk.

        A `block()` that consulted only `_seen` would pass the test above and
        fail here, which is the whole of the difference between a limit and a
        guard on emptiness.
        """
        carryover = Carryover(limit=0)
        carryover.remember(found(("Ada Lovelace", "Person")))

        assert carryover.mentions() == ()
        assert carryover.block() == ""

    def test_a_negative_limit_is_refused_rather_than_read_as_off(self):
        with pytest.raises(ValueError, match="must be >= 0"):
            Carryover(limit=-1)


class TestWhatCountsAsTheSameMention:
    def test_two_spellings_of_one_name_occupy_one_slot(self):
        """The key is the normalized name, as `entity_id_for`'s is.

        Keyed on the raw name these would be two entries -- and they are one
        entity, so the prompt would list it twice and spend the bound on
        itself.
        """
        carryover = Carryover()
        carryover.remember(found(("Ada Lovelace", "Person")))
        carryover.remember(found(("ada  lovelace", "Person")))

        assert carryover.mentions() == (("Ada Lovelace", "Person"),)

    def test_the_first_spelling_is_the_one_carried(self):
        """Not the most recent. The list exists to converge later chunks onto
        one spelling, so it must not follow them."""
        carryover = Carryover()
        carryover.remember(found(("Ada Lovelace", "Person")))
        carryover.remember(found(("ADA LOVELACE", "Person")))

        assert carryover.mentions() == (("Ada Lovelace", "Person"),)

    def test_one_name_under_two_types_is_two_mentions(self):
        """The key is a tuple, so force a collision in each component.

        Keyed on the name alone, "Mercury" the planet would suppress "Mercury"
        the god -- two entities under `entity_id_for`, which nests the type.
        """
        carryover = Carryover()
        carryover.remember(found(("Mercury", "Planet"), ("Mercury", "Deity")))

        assert carryover.mentions() == (("Mercury", "Planet"), ("Mercury", "Deity"))

    def test_two_names_under_one_type_are_two_mentions(self):
        """The other component of the same tuple key.

        Keyed on the type alone this collapses to one, which no test using a
        distinct type for every entity could see.
        """
        carryover = Carryover()
        carryover.remember(found(("Ada Lovelace", "Person"), ("Charles Babbage", "Person")))

        assert carryover.mentions() == (
            ("Ada Lovelace", "Person"),
            ("Charles Babbage", "Person"),
        )


class TestTheBound:
    def test_the_most_recent_mentions_are_the_ones_kept(self):
        carryover = Carryover(limit=2)
        carryover.remember(found(("A", "Person"), ("B", "Person"), ("C", "Person")))

        assert carryover.mentions() == (("B", "Person"), ("C", "Person"))

    def test_a_repeated_mention_does_not_refresh_its_position(self):
        """The distinguishing case for "insert if absent" versus "move to end".

        Both implementations agree on every input where nothing repeats, which
        is every other test in this class. Here they disagree: refreshing on
        each mention would evict `B` -- the genuinely newer entity -- in favour
        of `A`, which is the opposite of what a recency bound is for, and it is
        the shape a document with one omnipresent protagonist actually has.
        """
        carryover = Carryover(limit=2)
        carryover.remember(found(("A", "Person")))
        carryover.remember(found(("B", "Person")))
        carryover.remember(found(("A", "Person"), ("C", "Person")))

        assert carryover.mentions() == (("B", "Person"), ("C", "Person"))

    def test_the_default_bound_is_not_unbounded(self):
        """Guards the guard: a `DEFAULT_CARRYOVER_ENTITIES` large enough to
        never bite would make every test above pass while the prompt grew with
        the document."""
        carryover = Carryover()
        carryover.remember(found(*((f"Person {i}", "Person") for i in range(200))))

        assert len(carryover.mentions()) == DEFAULT_CARRYOVER_ENTITIES
        assert carryover.mentions()[-1] == ("Person 199", "Person")


class TestTheBlock:
    def test_it_names_every_carried_entity_with_its_type(self):
        carryover = Carryover()
        carryover.remember(found(("Ada Lovelace", "Person"), ("Analytical Engine", "Machine")))

        block = carryover.block()

        assert "- Ada Lovelace (Person)" in block
        assert "- Analytical Engine (Machine)" in block

    def test_it_lists_oldest_first(self):
        carryover = Carryover()
        carryover.remember(found(("First", "Person"), ("Second", "Person")))

        block = carryover.block()

        assert block.index("First") < block.index("Second")

    def test_it_forbids_listing_an_entity_the_chunk_does_not_mention(self):
        """The sentence that stops this feature becoming a hallucination
        source. Asserted because a list of names with the instruction removed
        would satisfy every other test in this class."""
        carryover = Carryover()
        carryover.remember(found(("Ada Lovelace", "Person")))

        assert "Do NOT list an entity unless" in carryover.block()

    def test_it_is_appendable_to_a_prompt_without_running_into_it(self):
        """Appended, so it must not begin with the last word of a prompt."""
        carryover = Carryover()
        carryover.remember(found(("Ada Lovelace", "Person")))

        assert carryover.block().startswith("\n\n")
