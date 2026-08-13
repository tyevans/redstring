"""Turning what a model said into domain types it could not have produced.

The mapper supplies everything `Entity` requires and `ExtractedEntity` cannot
carry: identity, tenant, source attribution, normalized name, provenance. Two
of those choices are load-bearing and are pinned hardest here -- that ids are
**deterministic**, which is what lets two chunks agree about one person, and
that a relationship whose endpoint was never listed is **dropped and
counted**, which is what keeps a dangling edge out of `GraphStore`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from redstring.domain.blocking import blocking_keys_for
from redstring.domain.normalization import normalize_name
from redstring.domain.provenance import ExtractionMethod
from redstring.extraction.mapping import MappedExtraction, entity_id_for, map_extraction
from redstring.extraction.schema import (
    DEFAULT_CONFIDENCE,
    ExtractedEntity,
    ExtractedRelationship,
    Extraction,
)

TENANT = UUID("11111111-1111-1111-1111-111111111111")
OTHER_TENANT = UUID("22222222-2222-2222-2222-222222222222")
SOURCE = "doc-1"
MODEL = "ollama/qwen3.6-27b-mtp"

#: A fixed observation instant. Never `datetime.now(UTC)`: a fixture that
#: varies per run makes any comparison on `observed_at` non-deterministic.
OBSERVED = datetime(2026, 2, 4, 11, 7, tzinfo=UTC)


def mapped(extraction: Extraction, *, tenant=TENANT, source=SOURCE, model=MODEL):
    return map_extraction(
        extraction,
        tenant_id=tenant,
        source_id=source,
        model=model,
        reference_date=None,
        observed_at=OBSERVED,
    )


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
        assert ada.provenance.source_id == SOURCE
        assert ada.normalized_name == "ada lovelace"
        assert ada.provenance.extraction_method is ExtractionMethod.LLM
        assert ada.provenance.model == MODEL

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
        assert (ada.description, ada.provenance.confidence) == ("A mathematician.", 0.9)
        assert ada.properties == {"born": 1815}

    def test_an_unstated_confidence_is_the_midpoint_rather_than_certainty(self):
        """Reading silence as 1.0 would rank unmarked guesses above stated ones."""
        [ada] = mapped(Extraction(entities=[entity("Ada Lovelace")])).entities

        assert ada.provenance.confidence == DEFAULT_CONFIDENCE

    def test_a_blank_name_is_dropped_rather_than_crashing_the_extraction(self):
        """`Entity` refuses a blank name, and one bad row must not cost the rest.

        The alternative is a `ValidationError` that discards every other
        entity in a long document because the model emitted one empty string.
        """
        result = mapped(Extraction(entities=[entity("   "), entity("Ada Lovelace")]))

        assert [e.name for e in result.entities] == ["Ada Lovelace"]
        assert result.dropped_entities == 1

    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param(lambda: entity("Ada\x00Lovelace"), id="in-the-name"),
            pytest.param(lambda: entity("Bad", entity_type="per\x00son"), id="in-the-type"),
            pytest.param(
                lambda: entity("Bad", description="a\x00b"),
                id="in-the-description",
            ),
            pytest.param(
                lambda: entity("Bad", properties={"note": "a\x00b"}),
                id="in-a-property-value",
            ),
            pytest.param(
                lambda: entity("Bad", properties={"a\x00b": "note"}),
                id="in-a-property-key",
            ),
        ],
    )
    def test_a_nul_anywhere_is_dropped_rather_than_crashing_the_extraction(self, bad):
        """A model can emit a NUL, and no JSON-backed store can hold one.

        `Entity` refuses it (`domain/json_safety.py`), so without an explicit
        guard here that refusal arrives as a `ValidationError` out of
        `map_extraction` and costs every other row in the document -- which is
        precisely what the blank-name guard above exists to prevent, for a
        cause the model is equally responsible for.

        The good entity is stated **after** the bad one in every case: on a
        one-element input a drop and a crash are distinguishable, but a guard
        that stopped the loop rather than skipping the row would look correct.
        """
        result = mapped(Extraction(entities=[bad(), entity("Ada Lovelace")]))

        assert [e.name for e in result.entities] == ["Ada Lovelace"]
        assert result.dropped_entities == 1

    def test_a_lone_surrogate_is_dropped_too(self):
        """The failure that is not about storage at all.

        A lone surrogate has no UTF-8 encoding, and `entity_id_for` hashes
        with `uuid5`, which encodes -- so this raised `UnicodeEncodeError` out
        of the mapper before any store was involved, taking the whole chunk
        with it. `json.loads` builds one from an escape without complaint, so
        it is ordinary model output rather than an exotic input.

        Found by the mutation wrapper's baseline run rather than by review:
        the property that draws entity names had been given a hand-written
        alphabet that widened past `st.text()`'s default and started
        generating them.
        """
        result = mapped(Extraction(entities=[entity("Ada\ud800"), entity("Ada Lovelace")]))

        assert [e.name for e in result.entities] == ["Ada Lovelace"]
        assert result.dropped_entities == 1

    def test_the_drop_is_counted_apart_from_its_siblings(self):
        """Four counters summed from one loop can be wired to each other's
        fields and still add up; only input that moves them differently says
        which line feeds which."""
        result = mapped(Extraction(entities=[entity("a\x00b"), entity("Ada Lovelace")]))

        assert result.dropped_entities == 1
        assert (result.unresolved_relationships, result.self_loops) == (0, 0)
        assert result.undatable_relative == 0


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

    def test_an_edge_records_which_document_stated_it(self):
        """Provenance on the edge, not only on its endpoints.

        Asserted against a *second* document as well, because `SOURCE` is the
        only source id most of this file uses -- an implementation that hard
        coded it, or that read the source from an endpoint entity, would agree
        with a one-document assertion.
        """
        extraction = Extraction(
            entities=[entity("Ada Lovelace"), entity("Charles Babbage")],
            relationships=[link("Ada Lovelace", "Charles Babbage")],
        )

        [here] = mapped(extraction).relationships
        [elsewhere] = mapped(extraction, source="doc-2").relationships

        assert here.source_id == SOURCE
        assert elsewhere.source_id == "doc-2"

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

    def test_a_self_loop_does_not_stop_the_edges_after_it(self):
        """Found by cosmic-ray: `continue` mutated to `break` and survived.

        Every other self-loop test states exactly one relationship, and with
        one item left in the loop `break` and `continue` are the same
        function -- the CLAUDE.md failure shape exactly. A bad edge must skip
        itself and nothing else, or one careless model sentence silently
        discards every relationship listed after it.
        """
        result = mapped(
            Extraction(
                entities=[entity("Ada Lovelace"), entity("Charles Babbage")],
                relationships=[
                    link("Ada Lovelace", "ada lovelace", "IS"),
                    link("Ada Lovelace", "Charles Babbage", "WORKED_WITH"),
                ],
            )
        )

        assert [edge.relationship_type for edge in result.relationships] == ["WORKED_WITH"]
        assert result.self_loops == 1

    def test_an_unresolved_edge_does_not_stop_the_edges_after_it(self):
        """The same shape one branch up, and equally invisible with one edge."""
        result = mapped(
            Extraction(
                entities=[entity("Ada Lovelace"), entity("Charles Babbage")],
                relationships=[
                    link("Ada Lovelace", "Someone Unmentioned", "KNEW"),
                    link("Ada Lovelace", "Charles Babbage", "WORKED_WITH"),
                ],
            )
        )

        assert [edge.relationship_type for edge in result.relationships] == ["WORKED_WITH"]
        assert result.unresolved_relationships == 1

    def test_a_blank_name_does_not_stop_the_entities_after_it(self):
        """And once more for the entity loop, for the same reason."""
        result = mapped(
            Extraction(entities=[entity("  "), entity("Ada"), entity("  "), entity("Charles")])
        )

        assert sorted(e.name for e in result.entities) == ["Ada", "Charles"]
        assert result.dropped_entities == 2

    def test_a_relationship_gets_a_deterministic_id_too(self):
        """So re-extraction upserts the same edge instead of accumulating copies."""
        payload = Extraction(
            entities=[entity("Ada Lovelace"), entity("Charles Babbage")],
            relationships=[link("Ada Lovelace", "Charles Babbage")],
        )

        [first] = mapped(payload).relationships
        [again] = mapped(payload).relationships

        assert first.id == again.id

    def test_a_duplicated_edge_keeps_the_more_confident_statement(self):
        """`setdefault` ignored confidence entirely: first mention won.

        A model that states the same edge twice, hedged and then certain, had
        the hedge recorded. Worse, `merge_extractions` used a *different* rule
        and kept the confident one -- so "dedup within one answer" and "dedup
        across chunks" disagreed, which is the inconsistency the unified
        `preference` was introduced to remove for entities and left in place
        for edges.
        """
        result = mapped(
            Extraction(
                entities=[entity("Ada Lovelace"), entity("Charles Babbage")],
                relationships=[
                    link("Ada Lovelace", "Charles Babbage", "KNOWS", confidence=0.2),
                    link("Ada Lovelace", "Charles Babbage", "KNOWS", confidence=0.9),
                ],
            )
        )

        assert [edge.confidence for edge in result.relationships] == [0.9]

    def test_it_still_keeps_the_more_confident_one_when_it_comes_first(self):
        result = mapped(
            Extraction(
                entities=[entity("Ada Lovelace"), entity("Charles Babbage")],
                relationships=[
                    link("Ada Lovelace", "Charles Babbage", "KNOWS", confidence=0.9),
                    link("Ada Lovelace", "Charles Babbage", "KNOWS", confidence=0.2),
                ],
            )
        )

        assert [edge.confidence for edge in result.relationships] == [0.9]

    def test_a_tie_on_edge_confidence_is_broken_without_letting_order_decide(self):
        """The common case, because every unscored edge carries DEFAULT_CONFIDENCE.

        Within one id bucket the endpoints and the type are fixed -- all three
        feed `_relationship_id_for` -- so `properties` is the only other field
        that can differ, and it is what the order has to reach to be total.
        The failure this prevents: the same document extracted twice yields
        different `DocumentExtracted` payloads, in a durable replayable log.
        """
        rich = link("Ada Lovelace", "Charles Babbage", "KNOWS", properties={"evidence": "letters"})
        bare = link("Ada Lovelace", "Charles Babbage", "KNOWS")
        pair = [entity("Ada Lovelace"), entity("Charles Babbage")]

        forwards = mapped(Extraction(entities=pair, relationships=[rich, bare])).relationships
        backwards = mapped(Extraction(entities=pair, relationships=[bare, rich])).relationships

        assert [edge.properties for edge in forwards] == [{"evidence": "letters"}]
        assert [edge.properties for edge in backwards] == [{"evidence": "letters"}]

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

    def test_a_tie_on_confidence_is_broken_without_letting_order_decide(self):
        """Found by cosmic-ray: `>` mutated to `>=` and every test still passed.

        They all used *distinct* confidences, so no tie ever arose -- the
        CLAUDE.md failure shape again. And ties are the common case, not the
        edge case: every entity the model declines to score carries
        `DEFAULT_CONFIDENCE`, so a tie-break that fell back to "keep whichever
        came first" would make the same document map differently depending on
        the order the model happened to list things in.
        """
        terse = entity("Ada Lovelace", description="A.")
        fuller = entity("ada lovelace", description="A mathematician.")

        forwards = mapped(Extraction(entities=[terse, fuller])).entities
        backwards = mapped(Extraction(entities=[fuller, terse])).entities

        assert [e.description for e in forwards] == ["A mathematician."]
        assert [e.description for e in backwards] == ["A mathematician."]

    def test_an_absent_description_and_an_empty_one_resolve_the_same_way(self):
        """Found by the strengthened order-independence property, first run.

        `description or ""` maps `None` and `""` onto one value, so two
        mentions differing only in which they carry tied on *every* field of
        the order and arrival order decided -- while the two `Entity` objects
        are genuinely different. Pinned as an example as well as a property,
        because the property's minimal counterexample is one nobody would
        think to write by hand.
        """
        absent = entity("Ada Lovelace")
        empty = entity("Ada Lovelace", description="")

        forwards = mapped(Extraction(entities=[absent, empty])).entities
        backwards = mapped(Extraction(entities=[empty, absent])).entities

        assert [e.description for e in forwards] == [e.description for e in backwards]

    def test_two_mentions_tied_on_everything_but_name_still_resolve_the_same_way(self):
        """The last field of the order, so the order really is total.

        Two entities with one id can differ in `name` while normalizing
        together, and if the comparison ran out of tie-breakers before that
        the dict's insertion order would decide.
        """
        upper = entity("ADA LOVELACE")
        lower = entity("ada lovelace")

        forwards = mapped(Extraction(entities=[upper, lower])).entities
        backwards = mapped(Extraction(entities=[lower, upper])).entities

        assert [e.name for e in forwards] == [e.name for e in backwards]


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
            reference_date=None,
            observed_at=OBSERVED,
            method=ExtractionMethod.PATTERN,
        ).entities

        assert ada.provenance.model is None
        assert ada.provenance.extraction_method is ExtractionMethod.PATTERN

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
                reference_date=None,
                observed_at=OBSERVED,
            )


class TestTheResultTypeItself:
    def test_the_counters_default_to_zero_when_a_caller_builds_one_directly(self):
        """`MappedExtraction` is a public type, and nothing constructed it bare.

        Found by cosmic-ray: mutating `unresolved_relationships: int = 0` to
        `= 1` and `self_loops: int = 0` to `= -1` both survived, because every
        test reached the type through `map_extraction`, which passes all five
        fields explicitly. A caller assembling one by hand -- which the type's
        signature invites -- would have got a non-zero count out of an empty
        result.
        """
        empty = MappedExtraction(entities=[], relationships=[])

        assert empty.dropped_entities == 0
        assert empty.unresolved_relationships == 0
        assert empty.self_loops == 0


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

    # Unstorable text excluded for the reason `test_merging.py::DESCRIPTIONS`
    # gives: a candidate carrying it is dropped, so there would be no id to
    # check. `codec="utf-8"` is load-bearing -- without it `st.characters()`
    # generates unpaired surrogates, which is how the real defect this
    # property found got in.
    _NAMES = st.text(
        alphabet=st.characters(codec="utf-8", exclude_characters="\x00"), min_size=1
    ).filter(lambda s: s.strip())

    @given(name=_NAMES)
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


class TestBlockingKeys:
    """Mapped entities carry their blocking keys.

    Computed at extraction time and stored, because `GraphStore` computes
    nothing -- `find_by_blocking_key` only looks them up. An entity mapped
    without them is invisible to consolidation, and that failure is silent:
    blocking returns an empty candidate list, which is exactly what "this
    entity has no duplicates" also looks like.
    """

    def test_a_mapped_entity_carries_keys(self):
        [built] = mapped(Extraction(entities=[entity("Ada Lovelace")])).entities

        assert built.blocking_keys == blocking_keys_for(built)
        assert built.blocking_keys

    def test_two_mentions_of_one_name_get_the_same_keys(self):
        """The property blocking rests on. If it were false, a re-extraction
        would land an entity in a different block from its own earlier copy."""
        first = mapped(Extraction(entities=[entity("Ada Lovelace")]), source="doc-1")
        second = mapped(Extraction(entities=[entity("Ada Lovelace")]), source="doc-2")

        assert first.entities[0].blocking_keys == second.entities[0].blocking_keys
        # ...and they are nonetheless different entities, which is what gives
        # consolidation something to merge.
        assert first.entities[0].id != second.entities[0].id

    async def test_consolidation_can_find_a_mapped_entity_by_its_keys(self):
        """The seam, checked end to end rather than by matching two constants.

        Extraction writing keys and blocking reading them are in sibling layers
        that never import each other, so nothing but a test spanning both can
        tell that they agree about what a key is.
        """
        from redstring.graph.adapters.memory import InMemoryGraphStore

        [built] = mapped(Extraction(entities=[entity("Ada Lovelace")])).entities
        store = InMemoryGraphStore()
        await store.upsert_entity(built)

        for key in built.blocking_keys:
            found = await store.find_by_blocking_key(key, built.tenant_id)
            assert [e.id for e in found] == [built.id], key

    @given(
        names=st.lists(
            st.sampled_from(["Ada Lovelace", "ada lovelace", "  ADA   Lovelace "]),
            min_size=2,
            max_size=4,
        ),
        confidences=st.lists(st.floats(0.0, 1.0), min_size=4, max_size=4),
    )
    def test_mentions_in_one_bucket_agree_on_every_derived_field(self, names, confidences):
        """The premise `domain/preference.py`'s totality argument rests on.

        `normalized_name` and `blocking_keys` are not in the tie-break order,
        and that is only safe because two mentions of one entity cannot
        disagree about them -- both are pure functions of `name` and
        `entity_type`, which are the inputs to `entity_id_for`. The names here
        differ in case and whitespace *and still share an id*, which is the
        case that would break it if either function stopped normalizing.

        Checked rather than asserted, because the paragraph it supports is
        what makes a `>` -> `>=` mutant on that order equivalent rather than
        live -- and because the paragraph was wrong once already.
        """
        # Mapped one per call, so every mention survives and can be compared.
        # Deduplicating them first would leave one entity and nothing to
        # compare it against, which is how a test of this shape ends up
        # asserting only that dedup happened.
        built = [
            mapped(
                Extraction(
                    entities=[entity(name, confidence=confidences[index % len(confidences)])]
                )
            ).entities[0]
            for index, name in enumerate(names)
        ]

        assert len({e.id for e in built}) == 1, "the names should share one id"
        assert len({e.normalized_name for e in built}) == 1
        assert len({e.blocking_keys for e in built}) == 1
        # The fields that *may* differ, so the test is not vacuous: if these
        # were also constant the assertions above would say nothing about
        # derivation.
        assert {e.name for e in built} == set(names)

    def test_an_entity_whose_name_has_no_letters_still_has_keys(self):
        """The soundex key is absent for it, and the other two are not -- so it
        is still blockable. An entity with no keys at all cannot be
        consolidated by any path."""
        [built] = mapped(Extraction(entities=[entity("2024")])).entities

        assert built.blocking_keys
        assert not any(key.startswith("s:") for key in built.blocking_keys)
