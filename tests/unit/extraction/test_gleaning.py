"""The second pass: what it is told, and what is done with what it says."""

from __future__ import annotations

from redstring.extraction.gleaning import (
    MAX_LISTED,
    combine,
    found_nothing,
    gleaning_prompt,
)
from redstring.extraction.schema import ExtractedEntity, ExtractedRelationship, Extraction

BASE = "You extract a knowledge graph from text."


def answer(*names: str, links: list[tuple[str, str, str]] | None = None) -> Extraction:
    return Extraction(
        entities=[ExtractedEntity(name=name, entity_type="Person") for name in names],
        relationships=[
            ExtractedRelationship(source_name=a, target_name=b, relationship_type=kind)
            for a, b, kind in (links or [])
        ],
    )


class TestWhatTheSecondPassIsTold:
    def test_the_first_passs_prompt_is_kept_in_front(self):
        """A domain schema's vocabulary shaped the first answer and has to
        shape the second, or one chunk is extracted under two specifications
        and the results merged."""
        prompt = gleaning_prompt("Extract only mathematicians.", answer("Ada Lovelace"))

        assert prompt.startswith("Extract only mathematicians.")

    def test_it_names_the_entities_already_found(self):
        prompt = gleaning_prompt(BASE, answer("Ada Lovelace", "Charles Babbage"))

        assert "- Ada Lovelace (Person)" in prompt
        assert "- Charles Babbage (Person)" in prompt

    def test_it_names_the_relationships_already_found(self):
        """Listing only entities would invite the model to re-state every edge
        it already gave, which is the bulk of a second answer's tokens."""
        prompt = gleaning_prompt(BASE, answer("Ada", "Charles", links=[("Ada", "Charles", "KNEW")]))

        assert "- Ada -KNEW-> Charles" in prompt

    def test_an_empty_first_answer_says_so_rather_than_listing_nothing(self):
        """A trailing blank where the list should be reads to a model as a
        formatting error, not as "you found nothing"."""
        prompt = gleaning_prompt(BASE, Extraction())

        assert "(nothing)" in prompt

    def test_it_still_asks_for_what_was_missed_when_nothing_was_found(self):
        assert "missed" in gleaning_prompt(BASE, Extraction())

    def test_the_list_is_bounded(self):
        """Guards the guard: an unbounded list grows with the chunk's entity
        count, and the pathological chunk is exactly the one a second pass is
        reached for."""
        prompt = gleaning_prompt(BASE, answer(*(f"Person {i}" for i in range(MAX_LISTED + 20))))

        listed = [line for line in prompt.splitlines() if line.startswith("- ")]
        assert len(listed) == MAX_LISTED

    def test_repeating_is_explicitly_permitted(self):
        """The opposite instruction to `carryover`'s, and deliberately so: a
        repeat is deduplicated by derived id, while a model told sternly not
        to repeat itself also withholds the entity it wants to re-type."""
        assert "not a problem to repeat" in gleaning_prompt(BASE, answer("Ada"))


class TestCombining:
    def test_both_answers_survive(self):
        combined = combine(answer("Ada"), answer("Charles"))

        assert [e.name for e in combined.entities] == ["Ada", "Charles"]

    def test_the_first_answer_comes_first(self):
        """Not because the fold depends on it -- `domain.preference` is a
        total order, so it does not -- but because a caller reading a combined
        answer should see the passes in the order they happened."""
        combined = combine(answer("First"), answer("Second"))

        assert combined.entities[0].name == "First"

    def test_relationships_from_both_answers_survive(self):
        combined = combine(
            answer("Ada", "Charles", links=[("Ada", "Charles", "KNEW")]),
            answer("Luigi", links=[("Charles", "Luigi", "LECTURED")]),
        )

        assert [r.relationship_type for r in combined.relationships] == ["KNEW", "LECTURED"]

    def test_a_repeat_is_not_removed_here(self):
        """Deduplication is the mapper's, by derived id. A second definition
        of "the same entity" applied first could only disagree with it."""
        combined = combine(answer("Ada"), answer("Ada"))

        assert len(combined.entities) == 2

    def test_an_edge_spanning_the_two_passes_is_resolvable(self):
        """The reason this combines wire shapes rather than mapped results.

        `_map_relationships` resolves an endpoint against entities in the same
        answer, so this edge -- one endpoint from each pass -- resolves only if
        the two passes reach the mapper as one `Extraction`.
        """
        combined = combine(
            answer("Ada"),
            answer("Charles", links=[("Ada", "Charles", "KNEW")]),
        )

        names = {e.name for e in combined.entities}
        edge = combined.relationships[0]
        assert {edge.source_name, edge.target_name} <= names


class TestTheStopCondition:
    def test_an_empty_pass_stops(self):
        assert found_nothing(Extraction())

    def test_a_pass_that_found_only_entities_does_not_stop(self):
        assert not found_nothing(answer("Ada"))

    def test_a_pass_that_found_only_relationships_does_not_stop(self):
        """The other side of the `and`.

        With `or` in place of `and` this returns True and the loop stops on a
        pass that found edges -- and no test where both lists are populated
        can tell the two spellings apart.
        """
        only_edges = Extraction(
            relationships=[
                ExtractedRelationship(
                    source_name="Ada", target_name="Charles", relationship_type="KNEW"
                )
            ]
        )

        assert not found_nothing(only_edges)
