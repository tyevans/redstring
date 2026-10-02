"""Tests for resolution similarity bands and LLM adjudication.

The names here are chosen against real Jaro-Winkler numbers:
- "Ada Lovelace" vs "Ada Lovelaxx" scores 0.933 (high band, above 0.92 merge threshold)
- "Ada Lovelace" vs "Ada Lovegood" scores 0.867 (band in between, model decides)
- "Ada Lovelace" vs "Zebedee Quill" scores 0.467 (low band, rejected without model call)
"""

from __future__ import annotations

from uuid import uuid4

from redstring.consolidation.policy import AdjudicationBatch, AdjudicationVerdict, Adjudicator
from redstring.events.merge import EntitiesMerged

from .conftest import Rig, keyed
from .test_policy import FakeProvider


def verdict(same=True, confidence=0.9, reason="same person") -> AdjudicationVerdict:
    return AdjudicationVerdict(same=same, confidence=confidence, reason=reason)


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
        """The judgement is an event, so it can be audited and undone."""
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
        """`merge_reason` records why a judgement went the way it did."""
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        await rig.seed(subject, keyed(tenant, "Ada Lovelace"))

        merged = await rig.service.resolve(subject, finder=rig.finder)

        assert merged.merge_reason


class TestTheLowBand:
    async def test_an_unalike_candidate_is_neither_merged_nor_asked_about(self):
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
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
            answers=[AdjudicationBatch(verdicts=[verdict(True, reason="same person")])]
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
            answers=[AdjudicationBatch(verdicts=[verdict(False, reason="two people")])]
        )

        merged = await rig.service.resolve(
            subject, finder=rig.finder, adjudicator=Adjudicator(provider)
        )

        assert merged is None
        assert await rig.events() == []

    async def test_without_an_adjudicator_the_band_is_rejected_not_merged(self):
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        await rig.seed(subject, keyed(tenant, "Ada Lovegood"))

        assert await rig.service.resolve(subject, finder=rig.finder) is None

    async def test_a_provider_outage_merges_nothing(self):
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
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        certain = keyed(tenant, "Ada Lovelace")
        ambiguous = keyed(tenant, "Ada Lovegood")
        await rig.seed(subject, certain, ambiguous)
        provider = FakeProvider(answers=[AdjudicationBatch(verdicts=[verdict(True)])])

        merged = await rig.service.resolve(
            subject, finder=rig.finder, adjudicator=Adjudicator(provider)
        )

        assert set(merged.merged_entity_ids) == {certain.id, ambiguous.id}
        assert len([e for e in await rig.events() if isinstance(e, EntitiesMerged)]) == 1

    async def test_only_the_band_reaches_the_model(self):
        rig, tenant = Rig(), uuid4()
        subject = keyed(tenant, "Ada Lovelace")
        certain = keyed(tenant, "Ada Lovelace")
        ambiguous = keyed(tenant, "Ada Lovegood")
        unalike = keyed(tenant, "Zebedee Quill")
        await rig.seed(subject, certain, ambiguous, unalike)
        provider = FakeProvider(answers=[AdjudicationBatch(verdicts=[verdict(False)])])

        await rig.service.resolve(subject, finder=rig.finder, adjudicator=Adjudicator(provider))

        [prompt] = provider.prompts
        assert "Ada Lovegood" in prompt
        assert "Zebedee Quill" not in prompt, "the low band cost a model call"
        assert prompt.count("Pair ") == 1, "the high band cost a model call"
