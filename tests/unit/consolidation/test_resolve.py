"""The whole pipeline: block, score, band, adjudicate, emit. B40, closed.

The names here are chosen against the real Jaro-Winkler numbers rather than by
eye, because the bands are narrow and intuition is wrong about them: against
"Ada Lovelace", the string "Ada Lovelaxx" scores 0.933 -- *above* the 0.92
merge threshold, not in the ambiguous band, despite looking like a typo. The
band cases use "Ada Lovegood" (0.867) and the reject cases "Zebedee Quill"
(0.467).


What slice 6 deleted resolved entities *inside extraction*: no event, nothing
to audit, nothing to undo, and the three `ConsolidationLog` invariants
bypassed. These tests exercise the rebuilt policy at the point where a
judgement becomes a fact in the log -- which is the difference that made
porting the old code the wrong move.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from eventsource.adapters.memory import (
    InMemoryCheckpointRepository,
    InMemoryDLQRepository,
    InMemoryEventStore,
    InMemorySnapshotStore,
)
from eventsource.application.projections import replay

from redstring.consolidation.candidates import CandidateFinder
from redstring.consolidation.policy import AdjudicationBatch, Adjudicator
from redstring.consolidation.service import ConsolidationService
from redstring.domain.blocking import blocking_keys_for
from redstring.domain.exceptions import MissingEntityError
from redstring.domain.ids import EntityId
from redstring.domain.merge_strategy import PropertyMergePolicy, PropertyMergeStrategy
from redstring.events.merge import EntitiesMerged
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.projections import GraphProjection

from .conftest import entity
from .test_policy import FakeProvider, _verdict


def keyed(tenant_id, name, **overrides):
    built = entity(tenant_id, name=name, **overrides)
    return built.model_copy(update={"blocking_keys": blocking_keys_for(built)})


class Rig:
    def __init__(self) -> None:
        self.event_store = InMemoryEventStore()
        self.graph_store = InMemoryGraphStore()
        self.projection = GraphProjection(
            self.graph_store,
            checkpoint_repo=InMemoryCheckpointRepository(),
            dlq_repo=InMemoryDLQRepository(),
        )
        self.service = ConsolidationService(
            event_store=self.event_store,
            snapshot_store=InMemorySnapshotStore(),
            graph_store=self.graph_store,
        )
        self.finder = CandidateFinder(self.graph_store, use_graph_signal=False)

    async def seed(self, *entities):
        """Entities straight into the store.

        The graph is a read model, so writing it directly is what a projection
        would have done -- and it keeps these tests about the policy rather
        than about the extraction aggregate, which
        `test_merge_undo_round_trip.py` already covers end to end.
        """
        await self.graph_store.upsert_entities(list(entities))

    async def events(self):
        return [envelope.event async for envelope in self.event_store.read_all()]

    async def catch_up(self):
        report = await replay(self.event_store, [self.projection])
        assert report.failed == 0


class TestTheHighBand:
    async def test_a_confident_duplicate_merges_with_no_model_call(self):
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        duplicate = keyed(tenant, "Ada Lovelace")
        await rig.seed(subject, duplicate)
        provider = FakeProvider()

        merged = await rig.service.resolve(
            subject, finder=rig.finder, adjudicator=Adjudicator(provider)
        )

        assert merged is not None
        assert merged.merged_entity_ids == [duplicate.id]
        assert provider.prompts == [], "the high band must not cost a model call"

    async def test_the_merge_reaches_the_log_and_the_graph(self):
        """The whole point of rebuilding this here rather than in extraction:
        the judgement is an event, so it can be audited and undone."""
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        duplicate = keyed(tenant, "Ada Lovelace")
        await rig.seed(subject, duplicate)

        await rig.service.resolve(subject, finder=rig.finder)
        await rig.catch_up()

        assert [type(event).__name__ for event in await rig.events()] == ["EntitiesMerged"]
        assert await rig.graph_store.resolve_entity_ids([duplicate.id], tenant) == {
            duplicate.id: subject.id
        }

    async def test_the_reason_is_recorded(self):
        """`merge_reason` is the only record of *why* a judgement went the way
        it did, and an unaudited merge is what B40 exists to prevent."""
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        await rig.seed(subject, keyed(tenant, "Ada Lovelace"))

        merged = await rig.service.resolve(subject, finder=rig.finder)

        assert merged.merge_reason


class TestTheLowBand:
    async def test_an_unalike_candidate_is_neither_merged_nor_asked_about(self):
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        # Same type, so it blocks -- and nothing like the name, so it scores
        # below the low threshold. Blocking and deciding are different steps
        # and this is the case that separates them.
        await rig.seed(subject, keyed(tenant, "Zebedee Quill"))
        provider = FakeProvider()

        merged = await rig.service.resolve(
            subject, finder=rig.finder, adjudicator=Adjudicator(provider)
        )

        assert merged is None
        assert provider.prompts == []
        assert await rig.events() == []

    async def test_an_empty_tenant_emits_nothing(self):
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        await rig.seed(subject)

        assert await rig.service.resolve(subject, finder=rig.finder) is None
        assert await rig.events() == []


class TestTheBandInBetween:
    async def test_the_model_decides_and_a_yes_merges(self):
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        ambiguous = keyed(tenant, "Ada Lovegood")
        await rig.seed(subject, ambiguous)
        provider = FakeProvider(
            answers=[AdjudicationBatch(verdicts=[_verdict(True, reason="same person")])]
        )

        merged = await rig.service.resolve(
            subject, finder=rig.finder, adjudicator=Adjudicator(provider)
        )

        assert len(provider.prompts) == 1
        assert merged.merged_entity_ids == [ambiguous.id]
        assert "same person" in merged.merge_reason

    async def test_a_no_merges_nothing(self):
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        await rig.seed(subject, keyed(tenant, "Ada Lovegood"))
        provider = FakeProvider(
            answers=[AdjudicationBatch(verdicts=[_verdict(False, reason="two people")])]
        )

        merged = await rig.service.resolve(
            subject, finder=rig.finder, adjudicator=Adjudicator(provider)
        )

        assert merged is None
        assert await rig.events() == []

    async def test_without_an_adjudicator_the_band_is_rejected_not_merged(self):
        """The band exists because the score does not settle it. Treating
        "nobody asked" as a yes would merge exactly the pairs the model was
        there to protect."""
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        await rig.seed(subject, keyed(tenant, "Ada Lovegood"))

        assert await rig.service.resolve(subject, finder=rig.finder) is None

    async def test_a_provider_outage_merges_nothing(self):
        """Not "merges everything", and not "crashes the run" either. An
        unanswered question is not a yes."""
        from redstring.domain.exceptions import EmptyCompletionError

        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        await rig.seed(subject, keyed(tenant, "Ada Lovegood"))
        provider = FakeProvider(raises=EmptyCompletionError(model="fake/x"))

        merged = await rig.service.resolve(
            subject, finder=rig.finder, adjudicator=Adjudicator(provider)
        )

        assert merged is None


class TestTheWholeGroup:
    async def test_confident_and_adjudicated_matches_land_in_one_event(self):
        """One event, not one per pair. Two merges into one canonical entity
        would each compute their redirections against a different graph -- the
        second against one the first had already changed."""
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        certain = keyed(tenant, "Ada Lovelace")
        ambiguous = keyed(tenant, "Ada Lovegood")
        await rig.seed(subject, certain, ambiguous)
        provider = FakeProvider(answers=[AdjudicationBatch(verdicts=[_verdict(True)])])

        merged = await rig.service.resolve(
            subject, finder=rig.finder, adjudicator=Adjudicator(provider)
        )

        assert set(merged.merged_entity_ids) == {certain.id, ambiguous.id}
        assert len([e for e in await rig.events() if isinstance(e, EntitiesMerged)]) == 1

    async def test_only_the_band_reaches_the_model(self):
        """The cost control. Sending every blocked pair is quadratic in model
        calls, which is what made LLM-assisted resolution impractical."""
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        certain = keyed(tenant, "Ada Lovelace")
        ambiguous = keyed(tenant, "Ada Lovegood")
        unalike = keyed(tenant, "Zebedee Quill")
        await rig.seed(subject, certain, ambiguous, unalike)
        provider = FakeProvider(answers=[AdjudicationBatch(verdicts=[_verdict(False)])])

        await rig.service.resolve(subject, finder=rig.finder, adjudicator=Adjudicator(provider))

        [prompt] = provider.prompts
        assert "Ada Lovegood" in prompt
        assert "Zebedee Quill" not in prompt, "the low band cost a model call"
        assert prompt.count("Pair ") == 1, "the high band cost a model call"


class TestMergeDecidesFields:
    async def test_the_emitted_event_carries_a_resolution(self):
        """The wiring test: without it, `plan_properties` has no caller and
        this whole change is another unreached component."""
        rig, tenant = Rig(), uuid4()
        canonical = keyed(tenant, "Ada Lovelace", properties={"role": "mathematician"})
        absorbed = keyed(tenant, "Ada Lovelace", properties={"role": "analyst"})
        await rig.seed(canonical, absorbed)

        event = await rig.service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )

        assert event.resolution is not None
        assert event.resolution.entity_id == canonical.id
        assert event.resolution.before.properties == canonical.properties

    async def test_the_services_policy_decides(self):
        """A non-default policy must reach `plan_properties`. With the policy
        dropped on the floor the default applies and the canonical value
        wins, so this is the assertion that catches an ignored argument."""
        rig, tenant = Rig(), uuid4()
        canonical = keyed(tenant, "Ada Lovelace", properties={"role": "mathematician"})
        absorbed = keyed(tenant, "Ada Lovelace", properties={"role": "analyst"})
        await rig.seed(canonical, absorbed)
        service = ConsolidationService(
            event_store=rig.event_store,
            snapshot_store=InMemorySnapshotStore(),
            graph_store=rig.graph_store,
            merge_policy=PropertyMergePolicy(default=PropertyMergeStrategy.PREFER_MERGED),
        )

        event = await service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )

        assert event.resolution.after.properties == absorbed.properties

    async def test_a_per_call_policy_overrides_the_services(self):
        rig, tenant = Rig(), uuid4()
        canonical = keyed(tenant, "Ada Lovelace", properties={"role": "mathematician"})
        absorbed = keyed(tenant, "Ada Lovelace", properties={"role": "analyst"})
        await rig.seed(canonical, absorbed)

        event = await rig.service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
            policy=PropertyMergePolicy(default=PropertyMergeStrategy.PREFER_MERGED),
        )

        assert event.resolution.after.properties == absorbed.properties

    async def test_a_canonical_entity_with_no_row_is_refused(self):
        """The log and the graph disagreeing, which is what
        `MissingEntityError` names -- not a routine miss."""
        rig, tenant = Rig(), uuid4()
        absorbed = keyed(tenant, "Ada Lovelace")
        await rig.seed(absorbed)

        with pytest.raises(MissingEntityError):
            await rig.service.merge(
                tenant_id=tenant,
                canonical_entity_id=EntityId(uuid4()),
                merged_entity_ids=[absorbed.id],
            )

    async def test_an_absorbed_entity_with_no_row_is_tolerated(self):
        """`_apply_merge` already tolerates this when writing aliases; the
        plan must agree with it rather than refuse where the projection
        shrugs."""
        rig, tenant = Rig(), uuid4()
        canonical = keyed(tenant, "Ada Lovelace")
        await rig.seed(canonical)

        event = await rig.service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[EntityId(uuid4())],
        )

        assert event.resolution is not None


class TestWithinDocumentResolutionIsNotASpecialCase:
    async def test_two_mentions_in_one_document_merge_by_the_same_path(self):
        """B40's note, checked. `entity_id_for` namespaces ids per document, so
        "Ada Lovelace" and "Ada" in one document are two entities by
        construction -- and they reach this code with both happening to share a
        `source_id`, through no special branch.
        """
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace", source_id="doc-1")
        same_document = keyed(tenant, "Ada Lovelace", source_id="doc-1")
        await rig.seed(subject, same_document)

        merged = await rig.service.resolve(subject, finder=rig.finder)

        assert merged.merged_entity_ids == [same_document.id]

    async def test_a_cross_document_duplicate_takes_the_identical_path(self):
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace", source_id="doc-1")
        other_document = keyed(tenant, "Ada Lovelace", source_id="doc-2")
        await rig.seed(subject, other_document)

        merged = await rig.service.resolve(subject, finder=rig.finder)

        assert merged.merged_entity_ids == [other_document.id]


class TestResolveRespectsTheInvariants:
    async def test_an_already_merged_candidate_is_never_proposed(self):
        """Not caught by an exception -- excluded before it gets that far. A
        pipeline that proposed it would raise `DoubleMergeError` on an entity
        the caller never mentioned."""
        rig, tenant = Rig(), uuid4()
        first = keyed(tenant, "Ada Lovelace")
        second = keyed(tenant, "Ada Lovelace")
        third = keyed(tenant, "Ada Lovelace")
        await rig.seed(first, second, third)

        await rig.service.resolve(first, finder=rig.finder)
        await rig.catch_up()
        again = await rig.service.resolve(first, finder=rig.finder)

        assert again is None
        assert len([e for e in await rig.events() if isinstance(e, EntitiesMerged)]) == 1

    async def test_an_aliased_subject_resolves_to_its_canonical_rather_than_raising(self):
        """Reproduces the reported bug first: `MergeIntoAliasError` from a subject
        that had itself already been merged away, e.g. by a caller re-resolving
        entities read off a stale prior extraction.

        The fix -- if A was merged into B, consolidating around A means
        consolidating around B, since a merge is exactly the claim that they
        are one entity. `third` should end up merged into `canonical`, not
        into `absorbed`, and no exception should reach the caller for the
        ordinary case of an already-merged subject.
        """
        rig, tenant = Rig(), uuid4()
        canonical = keyed(tenant, "Ada Lovelace")
        absorbed = keyed(tenant, "Ada Lovelace")
        third = keyed(tenant, "Ada Lovelace")
        await rig.seed(canonical, absorbed, third)

        await rig.service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()

        event = await rig.service.resolve(absorbed, finder=rig.finder)

        assert event is not None
        assert event.canonical_entity_id == canonical.id
        assert event.merged_entity_ids == [third.id]

    async def test_a_two_deep_alias_chain_resolves_to_the_terminal_canonical(self):
        """A single-hop resolve would fix the reported case and leave this one
        broken: `absorbed` is an alias of `also_absorbed`, which is itself an
        alias of `canonical`. Passing `absorbed` as subject must still land the
        merge on `canonical`, the chain's actual end, not on `also_absorbed`.
        """
        rig, tenant = Rig(), uuid4()
        canonical = keyed(tenant, "Ada Lovelace")
        also_absorbed = keyed(tenant, "Ada Lovelace")
        absorbed = keyed(tenant, "Ada Lovelace")
        third = keyed(tenant, "Ada Lovelace")
        await rig.seed(canonical, also_absorbed, absorbed, third)

        # Built in this order deliberately: `ConsolidationLog.merge` refuses
        # to merge *into* an alias, so the chain has to be grown from its
        # near end outward -- `also_absorbed` absorbs `absorbed` while it is
        # still canonical, and only then does `canonical` absorb
        # `also_absorbed`. Merging `canonical` first would try to merge
        # *into* `also_absorbed` after it is already an alias, which
        # `ConsolidationLog` refuses (that refusal is what stops a cycle from
        # forming in the first place).
        await rig.service.merge(
            tenant_id=tenant,
            canonical_entity_id=also_absorbed.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()
        await rig.service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[also_absorbed.id],
        )
        await rig.catch_up()

        event = await rig.service.resolve(absorbed, finder=rig.finder)

        assert event is not None
        assert event.canonical_entity_id == canonical.id
        assert event.merged_entity_ids == [third.id]

    async def test_the_absorbed_entitys_own_relationships_and_properties_are_untouched(self):
        """Resolving `subject` to its canonical must not re-derive or re-apply
        anything about the entity it resolved through -- that already happened
        when the earlier merge made it an alias. This call only decides which
        entity a *new* duplicate belongs with.
        """
        rig, tenant = Rig(), uuid4()
        canonical = keyed(tenant, "Ada Lovelace", properties={"born": "1815"})
        absorbed = keyed(tenant, "Ada Lovelace", properties={"title": "Countess"})
        third = keyed(tenant, "Ada Lovelace")
        await rig.seed(canonical, absorbed, third)

        await rig.service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )
        await rig.catch_up()
        before = await rig.graph_store.get_entity(absorbed.id, tenant)

        await rig.service.resolve(absorbed, finder=rig.finder)
        await rig.catch_up()

        after = await rig.graph_store.get_entity(absorbed.id, tenant)
        assert after == before, "resolving through an alias must not rewrite the alias row"

    async def test_a_resolved_merge_can_be_undone(self):
        """The property the deleted in-extraction resolver could not have: a
        judgement made by a model is reversible."""
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        duplicate = keyed(tenant, "Ada Lovelace")
        await rig.seed(subject, duplicate)

        merged = await rig.service.resolve(subject, finder=rig.finder)
        await rig.catch_up()
        await rig.service.undo(tenant_id=tenant, merge_event_id=merged.event_id)
        await rig.catch_up()

        assert await rig.graph_store.resolve_entity_ids([duplicate.id], tenant) == {
            duplicate.id: duplicate.id
        }
