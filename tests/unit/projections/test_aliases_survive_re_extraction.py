"""A merge survives a later extraction of the same document. B34, closed.

This file was `test_known_gaps.py`, and every test in it asserted that the
code was **wrong** -- pinning BACKLOG B34, a gap the projection could not close
without a `GraphStore` change. The change landed with the first slice that
emits `EntitiesMerged`, so the assertions are inverted here rather than
deleted: the sequence they build is the one the defect needed, and it is worth
keeping a test that walks it.

The shape, which needs no redelivery, no reordering and no bus:

```
DocumentExtracted(doc-1, model=A)   entities e0,e1,e2 and edge e1->e2
EntitiesMerged(e1 into e0)          edge redirected to e0->e2
DocumentExtracted(doc-1, model=B)   the same edge, endpoints e1->e2 again
```

The log is in strict order and each event is delivered exactly once.
Re-extraction is not a pathology -- `Document.record_extraction` is keyed on
the model version precisely so a newer model can re-run a document. The fold
now resolves each endpoint through the alias table before writing, so the
third event writes `e0->e2` and the merge stands.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from eventsource.adapters.memory import InMemorySnapshotStore
from eventsource.domain.tenant_context import tenant_scope

from redstring.aggregates.repositories import (
    consolidation_repository,
    document_repository,
)
from redstring.domain.consolidation import RelationshipRedirection
from redstring.domain.entity import Entity, ExtractionMethod
from redstring.domain.relationship import Relationship
from redstring.events.streams import consolidation_stream, document_stream
from redstring.projections import project

from .conftest import fresh_rig

SOURCE_ID = "doc-1"
FIRST_MODEL = "ollama/qwen3.6-27b"
SECOND_MODEL = "ollama/qwen4-30b"


@pytest.fixture
def tenant_id():
    return uuid4()


def _entity(tenant_id, entity_id, name):
    return Entity(
        id=entity_id,
        tenant_id=tenant_id,
        name=name,
        normalized_name=name.lower(),
        entity_type="person",
        source_id=SOURCE_ID,
        extraction_method=ExtractionMethod.PATTERN,
        confidence=0.9,
    )


async def build_merge_then_re_extraction(rig, tenant_id, *, re_extract: bool):
    """A log in strict order: extract, merge, and optionally extract again.

    Module level rather than a fixture so both test classes can use it without
    one reaching into the other's methods, and so the sequence it builds is
    readable in one place -- it is the whole point of this file.
    """
    canonical, absorbed, outsider = uuid4(), uuid4(), uuid4()
    entities = [
        _entity(tenant_id, canonical, "Ada Lovelace"),
        _entity(tenant_id, absorbed, "A. Lovelace"),
        _entity(tenant_id, outsider, "Analytical Engine"),
    ]
    edge = Relationship(
        id=uuid4(),
        tenant_id=tenant_id,
        source_entity_id=absorbed,
        target_entity_id=outsider,
        relationship_type="worked_on",
        confidence=0.8,
    )

    documents = document_repository(rig.event_store)
    consolidations = consolidation_repository(rig.event_store, InMemorySnapshotStore())
    stream = document_stream(tenant_id=tenant_id, source_id=SOURCE_ID)

    async with tenant_scope(tenant_id):
        document = await documents.load_or_create(stream.aggregate_id)
        document.record_extraction(
            tenant_id=tenant_id,
            source_id=SOURCE_ID,
            model_version=FIRST_MODEL,
            entities=entities,
            relationships=[edge],
        )
        await documents.save(document)

        log = await consolidations.load_or_create(
            consolidation_stream(tenant_id=tenant_id).aggregate_id
        )
        log.merge(
            tenant_id=tenant_id,
            canonical_entity_id=canonical,
            merged_entity_ids=[absorbed],
            redirections=[
                RelationshipRedirection(
                    before=edge,
                    after=edge.model_copy(update={"source_entity_id": canonical}),
                )
            ],
        )
        await consolidations.save(log)

        if re_extract:
            # The same document, re-run under a newer model. `record_extraction`
            # is keyed on the model version precisely so this is allowed.
            document = await documents.load_or_create(stream.aggregate_id)
            document.record_extraction(
                tenant_id=tenant_id,
                source_id=SOURCE_ID,
                model_version=SECOND_MODEL,
                entities=entities,
                relationships=[edge],
            )
            await documents.save(document)

    return edge, canonical, absorbed, outsider


class TestALaterExtractionCannotRevertAMerge:
    """The B34 sequence, now producing the right answer."""

    async def test_the_merge_is_applied_correctly_before_the_re_extraction(self, tenant_id):
        """The control. Without this, the test below could be passing because
        the merge never worked rather than because it was preserved."""
        rig = fresh_rig()
        edge, canonical, _, outsider = await build_merge_then_re_extraction(
            rig, tenant_id, re_extract=False
        )

        report = await project(rig.event_store, rig.projections)
        assert report.failed == 0

        shape = await rig.shape([tenant_id])
        assert shape[str(tenant_id)]["edges"][str(edge.id)] == [
            str(canonical),
            str(outsider),
        ]

    async def test_a_re_extraction_preserves_the_merge(self, tenant_id):
        """The edge stays on the canonical entity. This is B34's fix.

        Asserted as an equality against the *whole* pair rather than "not the
        pre-merge pair": an implementation that resolved only the source and
        left the target alone would satisfy a negative assertion here, because
        the target was never the absorbed entity.
        """
        rig = fresh_rig()
        edge, canonical, _absorbed, outsider = await build_merge_then_re_extraction(
            rig, tenant_id, re_extract=True
        )

        report = await project(rig.event_store, rig.projections)
        assert report.failed == 0

        edges = (await rig.shape([tenant_id]))[str(tenant_id)]["edges"]
        assert edges[str(edge.id)] == [str(canonical), str(outsider)]

    async def test_the_absorbed_entity_still_exists_and_resolves(self, tenant_id):
        """A merge is not a delete. The entity survives, carrying an alias."""
        rig = fresh_rig()
        _edge, canonical, absorbed, _outsider = await build_merge_then_re_extraction(
            rig, tenant_id, re_extract=True
        )

        await project(rig.event_store, rig.projections)

        assert await rig.graph_store.get_entity(absorbed, tenant_id) is not None
        assert await rig.graph_store.resolve_entity_ids([absorbed], tenant_id) == {
            absorbed: canonical
        }
        recorded = await rig.graph_store.find_aliases(canonical, tenant_id)
        assert [alias.alias_entity_id for alias in recorded] == [absorbed]
        # The fold looks the name up in the store rather than inventing one.
        assert recorded[0].alias_name == "A. Lovelace"

    async def test_replay_reproduces_the_corrected_state_exactly(self, tenant_id):
        """A rebuild gives the same graph, aliases included.

        Kept from the version of this file that pinned the defect, where it
        showed the gap was deterministic rather than a race. It now carries a
        second load: alias ids are `uuid5` of the merge event and the absorbed
        entity, and a `uuid4` there would fail here and nowhere else.
        """
        rig = fresh_rig()
        await build_merge_then_re_extraction(rig, tenant_id, re_extract=True)

        await project(rig.event_store, rig.projections)
        live = await rig.dump([tenant_id])

        await rig.graph_store.delete_by_tenant(tenant_id)
        await rig.vector_store.delete_by_tenant(tenant_id)
        await project(rig.event_store, rig.projections)

        assert await rig.dump([tenant_id]) == live


class TestAnEdgeThatCollapsesOntoOneEntity:
    """Both endpoints absorbed by the same merge, then re-extracted.

    The merge drops the edge, because redirecting it would make a self-loop and
    `Relationship` rejects those outright -- that is what `after=None` on a
    `RelationshipRedirection` means. The re-extraction then carries the edge
    again with its original endpoints, both of which now resolve to the
    canonical entity.

    The fold deletes it rather than writing it, which is the only available
    answer: there is no `Relationship` value it could construct. Worth its own
    test because it is the one path through the resolver that does not end in
    an upsert, and because the alternative -- letting pydantic raise -- would
    send a perfectly ordinary event to the DLQ.
    """

    async def _log(self, rig, tenant_id, *, re_extract: bool):
        canonical, first, second = uuid4(), uuid4(), uuid4()
        entities = [
            _entity(tenant_id, canonical, "Ada Lovelace"),
            _entity(tenant_id, first, "A. Lovelace"),
            _entity(tenant_id, second, "Lovelace, Ada"),
        ]
        edge = Relationship(
            id=uuid4(),
            tenant_id=tenant_id,
            source_entity_id=first,
            target_entity_id=second,
            relationship_type="same_as",
            confidence=0.8,
        )

        documents = document_repository(rig.event_store)
        consolidations = consolidation_repository(rig.event_store, InMemorySnapshotStore())
        stream = document_stream(tenant_id=tenant_id, source_id=SOURCE_ID)

        async with tenant_scope(tenant_id):
            document = await documents.load_or_create(stream.aggregate_id)
            document.record_extraction(
                tenant_id=tenant_id,
                source_id=SOURCE_ID,
                model_version=FIRST_MODEL,
                entities=entities,
                relationships=[edge],
            )
            await documents.save(document)

            log = await consolidations.load_or_create(
                consolidation_stream(tenant_id=tenant_id).aggregate_id
            )
            log.merge(
                tenant_id=tenant_id,
                canonical_entity_id=canonical,
                merged_entity_ids=[first, second],
                # `after=None`: dropped, not moved.
                redirections=[RelationshipRedirection(before=edge, after=None)],
            )
            await consolidations.save(log)

            if re_extract:
                document = await documents.load_or_create(stream.aggregate_id)
                document.record_extraction(
                    tenant_id=tenant_id,
                    source_id=SOURCE_ID,
                    model_version=SECOND_MODEL,
                    entities=entities,
                    relationships=[edge],
                )
                await documents.save(document)

        return edge, canonical

    async def test_the_merge_drops_the_edge(self, tenant_id):
        """The control: without it, the test below could pass because the edge
        was never written rather than because it was dropped and stayed gone."""
        rig = fresh_rig()
        _edge, _canonical = await self._log(rig, tenant_id, re_extract=False)

        report = await project(rig.event_store, rig.projections)
        assert report.failed == 0

        assert (await rig.shape([tenant_id]))[str(tenant_id)]["edges"] == {}

    async def test_a_re_extraction_does_not_bring_it_back(self, tenant_id):
        rig = fresh_rig()
        edge, _canonical = await self._log(rig, tenant_id, re_extract=True)

        report = await project(rig.event_store, rig.projections)
        # Not merely "the edge is absent": a `Relationship` self-loop raises,
        # so a fold that tried to write one would fail the event instead, and
        # an absent edge would look identical. This is the assertion that
        # tells "deleted" from "poisoned".
        assert report.failed == 0
        assert (await rig.shape([tenant_id]))[str(tenant_id)]["edges"] == {}
        assert await rig.graph_store.get_relationships(edge.source_entity_id, tenant_id) == []


class TestTheEventStoreWasNeverTheProblem:
    """The log was always right; only the fold was wrong.

    Stated because the natural first reading of the sequence above is that the
    write side emitted something incoherent. It did not: both extractions and
    the merge are events the aggregates legitimately produced, in the order
    they were produced, and the log holds the whole truth.
    """

    async def test_the_log_holds_both_extractions_and_the_merge(self, tenant_id):
        rig = fresh_rig()
        await build_merge_then_re_extraction(rig, tenant_id, re_extract=True)

        types = [type(envelope.event).__name__ async for envelope in rig.event_store.read_all()]
        assert types == ["DocumentExtracted", "EntitiesMerged", "DocumentExtracted"]
