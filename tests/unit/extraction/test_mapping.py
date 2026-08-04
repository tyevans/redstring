"""Turning what a model said into domain types it could not have produced.

The mapper supplies everything `Entity` requires and `ExtractedEntity` cannot
carry: identity, tenant, source attribution, normalized name, provenance. Two
of those choices are load-bearing and are pinned hardest here -- that ids are
**deterministic**, which is what lets two chunks agree about one person, and
that a relationship whose endpoint was never listed is **dropped and
counted**, which is what keeps a dangling edge out of `GraphStore`.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kg_builder.domain.entity import ExtractionMethod
from kg_builder.domain.normalization import normalize_name
from kg_builder.extraction.mapping import entity_id_for, map_extraction
from kg_builder.extraction.schema import (
    DEFAULT_CONFIDENCE,
    ExtractedEntity,
    ExtractedRelationship,
    Extraction,
)

TENANT = UUID("11111111-1111-1111-1111-111111111111")
OTHER_TENANT = UUID("22222222-2222-2222-2222-222222222222")
SOURCE = "doc-1"
MODEL = "ollama/qwen3.6-27b-mtp"


def mapped(extraction: Extraction, *, tenant=TENANT, source=SOURCE, model=MODEL):
    return map_extraction(extraction, tenant_id=tenant, source_id=source, model=model)


def entity(name: str, entity_type: str = "Person", **kwargs) -> ExtractedEntity:
    return ExtractedEntity(name=name, entity_type=entity_type, **kwargs)


def link(source: str, target: str, kind: str = "KNOWS", **kwargs) -> ExtractedRelationship:
    return ExtractedRelationship(
        source_name=source, target_name=target, relationship_type=kind, **kwargs
    )


class TestEntities:
    def test_the_fields_a_model_cannot_supply_are_filled_in(self):
        [ada] = mapped(Extraction(entities=[entity("Ada Lovelace")])).entities

        assert ada.tenant_id == TENANT
        assert ada.source_id == SOURCE
        assert ada.normalized_name == "ada lovelace"
        assert ada.extraction_method is ExtractionMethod.LLM
        assert ada.model == MODEL

    def test_what_the_model_did_say_survives_unchanged(self):
        said = entity(
            "Ada Lovelace",
            "Mathematician",
            description="A mathematician.",
            confidence=0.9,
            properties={"born": 1815},
        )

        [ada] = mapped(Extraction(entities=[said])).entities

        assert (ada.name, ada.entity_type) == ("Ada Lovelace", "Mathematician")
        assert (ada.description, ada.confidence) == ("A mathematician.", 0.9)
        assert ada.properties == {"born": 1815}

    def test_an_unstated_confidence_is_the_midpoint_rather_than_certainty(self):
        """Reading silence as 1.0 would rank unmarked guesses above stated ones."""
        [ada] = mapped(Extraction(entities=[entity("Ada Lovelace")])).entities

        assert ada.confidence == DEFAULT_CONFIDENCE

    def test_a_blank_name_is_dropped_rather_than_crashing_the_extraction(self):
        """`Entity` refuses a blank name, and one bad row must not cost the rest.

        The alternative is a `ValidationError` that discards every other
        entity in a long document because the model emitted one empty string.
        """
        result = mapped(Extraction(entities=[entity("   "), entity("Ada Lovelace")]))

        assert [e.name for e in result.entities] == ["Ada Lovelace"]
        assert result.dropped_entities == 1


class TestIdentity:
    def test_the_same_name_and_type_get_the_same_id_every_time(self):
        """The property the whole cross-chunk merge rests on.

        Random ids would make every chunk's mention of Ada a different Ada,
        and no merge could tell them apart without re-deriving exactly this
        key -- at which point the key may as well *be* the id.
        """
        first = entity_id_for(tenant_id=TENANT, source_id=SOURCE, name="Ada", entity_type="Person")
        again = entity_id_for(tenant_id=TENANT, source_id=SOURCE, name="Ada", entity_type="Person")

        assert first == again

    def test_identity_survives_the_differences_normalization_erases(self):
        plain = entity_id_for(tenant_id=TENANT, source_id=SOURCE, name="Ada", entity_type="Person")
        noisy = entity_id_for(
            tenant_id=TENANT, source_id=SOURCE, name="  ADA  ", entity_type="Person"
        )

        assert plain == noisy

    def test_the_same_name_under_two_types_is_two_entities(self):
        """ "Mercury" the planet and "Mercury" the god are not one thing."""
        planet = entity_id_for(
            tenant_id=TENANT, source_id=SOURCE, name="Mercury", entity_type="Planet"
        )
        god = entity_id_for(tenant_id=TENANT, source_id=SOURCE, name="Mercury", entity_type="Deity")

        assert planet != god

    def test_two_tenants_naming_the_same_thing_get_two_entities(self):
        mine = entity_id_for(tenant_id=TENANT, source_id=SOURCE, name="Ada", entity_type="Person")
        yours = entity_id_for(
            tenant_id=OTHER_TENANT, source_id=SOURCE, name="Ada", entity_type="Person"
        )

        assert mine != yours

    def test_two_documents_naming_the_same_thing_get_two_entities(self):
        """Deduplicating *across* documents is consolidation's job, not this one.

        Merging them here would mean extraction silently deciding that two
        sources refer to one thing, which is the judgement `ConsolidationLog`
        exists to record, undo and audit.
        """
        here = entity_id_for(tenant_id=TENANT, source_id="doc-1", name="Ada", entity_type="Person")
        there = entity_id_for(tenant_id=TENANT, source_id="doc-2", name="Ada", entity_type="Person")

        assert here != there

    @given(
        left=st.tuples(st.text(min_size=1), st.text(min_size=1)),
        right=st.tuples(st.text(min_size=1), st.text(min_size=1)),
    )
    def test_no_pair_of_distinct_keys_collides_by_running_together(self, left, right):
        """The hazard ADR 0001 names for stream ids, in a four-part key.

        A scheme that joined the parts before hashing would map
        `("ab", "c")` and `("a", "bc")` onto one id, and two of these parts
        are free-form model output where a separator character cannot be
        ruled out. Nesting the `uuid5` calls means every hashed name is a
        single whole string, so this holds by construction rather than by
        choosing a lucky delimiter.
        """
        source_a, name_a = left
        source_b, name_b = right
        ids = {
            entity_id_for(tenant_id=TENANT, source_id=s, name=n, entity_type="Person")
            for s, n in (left, right)
        }

        collided = len(ids) == 1
        same_key = (source_a, normalize_name(name_a)) == (source_b, normalize_name(name_b))
        assert collided == same_key


class TestRelationships:
    def test_endpoints_are_resolved_from_names_to_the_ids_the_entities_got(self):
        result = mapped(
            Extraction(
                entities=[entity("Ada Lovelace"), entity("Charles Babbage")],
                relationships=[link("Ada Lovelace", "Charles Babbage", "WORKED_WITH")],
            )
        )
        by_name = {e.name: e.id for e in result.entities}

        [edge] = result.relationships
        assert edge.source_entity_id == by_name["Ada Lovelace"]
        assert edge.target_entity_id == by_name["Charles Babbage"]
        assert edge.relationship_type == "WORKED_WITH"

    def test_an_endpoint_is_matched_the_way_identity_is_and_not_by_exact_text(self):
        """The model routinely spells the endpoint differently from the entity.

        Requiring byte equality would drop most edges a real model produces,
        and would drop them *silently* into `unresolved_relationships` where
        it reads as the model failing to mention an entity.
        """
        result = mapped(
            Extraction(
                entities=[entity("Ada Lovelace"), entity("Charles Babbage")],
                relationships=[link("  ada lovelace ", "CHARLES BABBAGE")],
            )
        )

        assert len(result.relationships) == 1
        assert result.unresolved_relationships == 0

    def test_an_edge_to_something_never_listed_as_an_entity_is_dropped_and_counted(self):
        """`GraphStore.upsert_relationship` raises on a missing endpoint.

        Passing it through would turn one careless model sentence into a
        poison event that fails the whole projection.
        """
        result = mapped(
            Extraction(
                entities=[entity("Ada Lovelace")],
                relationships=[link("Ada Lovelace", "The Analytical Engine")],
            )
        )

        assert result.relationships == []
        assert result.unresolved_relationships == 1

    def test_a_self_loop_is_dropped_and_counted_separately(self):
        """`Relationship` forbids it, and the model produces it constantly.

        Counted apart from an unresolved endpoint because they say different
        things: this one means the model related a thing to itself, which is
        a prompt problem, not a missing entity.
        """
        result = mapped(
            Extraction(
                entities=[entity("Ada Lovelace")],
                relationships=[link("Ada Lovelace", "ada lovelace", "IS")],
            )
        )

        assert result.relationships == []
        assert result.self_loops == 1

    def test_two_names_that_normalize_together_make_one_entity_and_a_self_loop(self):
        """The case where the two drop reasons are genuinely hard to tell apart.

        Both endpoints resolve, so it is not unresolved; they resolve to the
        same id, so it is a self-loop. An implementation that checked for
        self-loops on the *names* would call this a valid edge and hand
        `Relationship` something it refuses.
        """
        result = mapped(
            Extraction(
                entities=[entity("Ada Lovelace"), entity("ADA LOVELACE")],
                relationships=[link("Ada Lovelace", "ADA LOVELACE")],
            )
        )

        assert len(result.entities) == 1
        assert (result.self_loops, result.unresolved_relationships) == (1, 0)

    def test_a_relationship_gets_a_deterministic_id_too(self):
        """So re-extraction upserts the same edge instead of accumulating copies."""
        payload = Extraction(
            entities=[entity("Ada Lovelace"), entity("Charles Babbage")],
            relationships=[link("Ada Lovelace", "Charles Babbage")],
        )

        [first] = mapped(payload).relationships
        [again] = mapped(payload).relationships

        assert first.id == again.id

    def test_two_relationship_types_between_one_pair_are_two_edges(self):
        result = mapped(
            Extraction(
                entities=[entity("Ada Lovelace"), entity("Charles Babbage")],
                relationships=[
                    link("Ada Lovelace", "Charles Babbage", "WORKED_WITH"),
                    link("Ada Lovelace", "Charles Babbage", "CORRESPONDED_WITH"),
                ],
            )
        )

        assert len({edge.id for edge in result.relationships}) == 2

    def test_direction_is_part_of_a_relationship_identity(self):
        """A -> B and B -> A are different claims and must not share an id."""
        result = mapped(
            Extraction(
                entities=[entity("Ada Lovelace"), entity("Charles Babbage")],
                relationships=[
                    link("Ada Lovelace", "Charles Babbage", "TAUGHT"),
                    link("Charles Babbage", "Ada Lovelace", "TAUGHT"),
                ],
            )
        )

        assert len({edge.id for edge in result.relationships}) == 2


class TestDuplicatesWithinOneCall:
    def test_the_same_entity_named_twice_becomes_one(self):
        result = mapped(Extraction(entities=[entity("Ada Lovelace"), entity("  ada   lovelace  ")]))

        assert len(result.entities) == 1

    def test_the_surviving_duplicate_is_the_more_confident_one(self):
        """Ordering must not decide it: the merge has to be commutative.

        Taking "first seen" would make the answer depend on the order the
        model happened to list them in, and the same document would extract
        differently on a re-run.
        """
        low = entity("Ada Lovelace", confidence=0.2, description="unsure")
        high = entity("ada lovelace", confidence=0.95, description="sure")

        forwards = mapped(Extraction(entities=[low, high])).entities
        backwards = mapped(Extraction(entities=[high, low])).entities

        assert [e.description for e in forwards] == ["sure"]
        assert [e.description for e in backwards] == ["sure"]


class TestTenantSafety:
    def test_every_mapped_object_carries_the_tenant_it_was_asked_for(self):
        """`DocumentExtracted` rejects a foreign tenant in its payload.

        This is the only place the tenant is applied, so a bug here is a
        cross-tenant write rather than a validation failure.
        """
        result = mapped(
            Extraction(
                entities=[entity("Ada Lovelace"), entity("Charles Babbage")],
                relationships=[link("Ada Lovelace", "Charles Babbage")],
            ),
            tenant=OTHER_TENANT,
        )

        assert {e.tenant_id for e in result.entities} == {OTHER_TENANT}
        assert {r.tenant_id for r in result.relationships} == {OTHER_TENANT}


class TestProvenance:
    def test_a_non_model_method_may_not_carry_a_model_string(self):
        """`Entity` enforces it; this asserts the mapper does not fight it."""
        [ada] = map_extraction(
            Extraction(entities=[entity("Ada Lovelace")]),
            tenant_id=TENANT,
            source_id=SOURCE,
            model=None,
            method=ExtractionMethod.PATTERN,
        ).entities

        assert ada.model is None
        assert ada.extraction_method is ExtractionMethod.PATTERN

    def test_an_llm_extraction_without_a_model_string_is_refused(self):
        """Provenance is the point of recording `model` at all.

        `Entity` permits `model=None` for an `LLM` extraction, so nothing
        below would complain -- the entities would simply land in the log
        with no record of what produced them, which is unrecoverable after
        the fact.
        """
        with pytest.raises(ValueError, match="model"):
            map_extraction(
                Extraction(entities=[entity("Ada Lovelace")]),
                tenant_id=TENANT,
                source_id=SOURCE,
                model=None,
            )


class TestEmptyIsNotAnError:
    def test_an_extraction_that_found_nothing_maps_to_nothing_without_complaint(self):
        result = mapped(Extraction())

        assert (result.entities, result.relationships) == ([], [])
        assert (result.dropped_entities, result.unresolved_relationships) == (0, 0)


class TestProperties:
    @given(
        names=st.lists(
            st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1),
            min_size=1,
            max_size=8,
        )
    )
    def test_mapping_the_same_payload_twice_gives_identical_ids(self, names):
        """Idempotence, which is what makes re-extraction an upsert not a fork."""
        payload = Extraction(entities=[entity(name) for name in names])

        first = [e.id for e in mapped(payload).entities]
        second = [e.id for e in mapped(payload).entities]

        assert first == second

    @given(
        names=st.lists(
            st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1),
            min_size=1,
            max_size=8,
            unique_by=normalize_name,
        ),
        seed=st.integers(),
    )
    def test_the_set_of_entities_does_not_depend_on_the_order_they_were_listed(self, names, seed):
        """A model lists entities in whatever order it read them.

        `unique_by=normalize_name` keeps the permutation honest: with
        duplicates present, "same set of ids" would hold even for an
        implementation that dropped whichever copy came second.
        """
        import random

        shuffled = list(names)
        random.Random(seed).shuffle(shuffled)

        forwards = {e.id for e in mapped(Extraction(entities=[entity(n) for n in names])).entities}
        backwards = {
            e.id for e in mapped(Extraction(entities=[entity(n) for n in shuffled])).entities
        }

        assert forwards == backwards

    @given(name=st.text(min_size=1).filter(lambda s: s.strip()))
    def test_every_mapped_entity_id_is_a_uuid5(self, name):
        [mapped_entity] = mapped(Extraction(entities=[entity(name)])).entities

        assert mapped_entity.id.version == 5


def test_a_generated_id_is_stable_across_runs_and_not_merely_within_one():
    """Pinned literally, because "deterministic" and "constant" differ.

    A `uuid5` seeded from anything process-local -- a `uuid4` namespace built
    at import, a `hash()` of the name -- passes every equality test above and
    still gives a different answer tomorrow, which silently forks every
    document's entities on the next deploy. Only a pinned value catches it.
    """
    assert entity_id_for(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        source_id="doc-1",
        name="Ada Lovelace",
        entity_type="Person",
    ) == UUID("899d1f1b-504f-5bbe-a266-a9ff5593dc91")


def test_the_pinned_id_is_not_an_accident_of_this_tenant():
    """Second anchor: the first would pass for an implementation ignoring inputs."""
    assert entity_id_for(
        tenant_id=uuid4(), source_id="doc-1", name="Ada Lovelace", entity_type="Person"
    ) != UUID("899d1f1b-504f-5bbe-a266-a9ff5593dc91")
