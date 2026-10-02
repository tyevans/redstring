"""Tests for within-document resolution and consolidation invariants.

Covers:
- Cross-document and within-document deduplication following the identical path
- Candidate exclusion for already-merged entities
- Aliased subject canonical resolution and transitive alias chains
- Isolation of absorbed entities' properties and relationships
- Reversibility of model judgements via undo
"""

from __future__ import annotations

from uuid import uuid4

from redstring.events.merge import EntitiesMerged

from .conftest import Rig, keyed


class TestWithinDocumentResolutionIsNotASpecialCase:
    async def test_two_mentions_in_one_document_merge_by_the_same_path(self):
        """Entities sharing a source_id follow the standard resolution path."""
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
        rig, tenant = Rig(), uuid4()
        canonical = keyed(tenant, "Ada Lovelace")
        also_absorbed = keyed(tenant, "Ada Lovelace")
        absorbed = keyed(tenant, "Ada Lovelace")
        third = keyed(tenant, "Ada Lovelace")
        await rig.seed(canonical, also_absorbed, absorbed, third)

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
