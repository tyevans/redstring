"""Defects the fold has today, pinned so they cannot be forgotten or repeated.

**Every test in this file asserts that the code is WRONG.** That is deliberate.
BACKLOG B34 describes a gap the projection cannot close without a change to
`GraphStore`, which belongs to slice 7. An assumption written only in a
docstring and a backlog entry is one nobody reads; a test that fails the day
the gap closes is one whoever closed it must read.

**If a test here starts failing, that is good news.** It means the fold has
gained the alias representation B34 asks for. Delete the test, close B34, and
say so in the commit.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from eventsource.adapters.memory import InMemorySnapshotStore
from eventsource.domain.tenant_context import tenant_scope

from kg_builder.aggregates.repositories import (
    consolidation_repository,
    document_repository,
)
from kg_builder.domain.consolidation import RelationshipRedirection
from kg_builder.domain.entity import Entity, ExtractionMethod
from kg_builder.domain.relationship import Relationship
from kg_builder.events.streams import consolidation_stream, document_stream
from kg_builder.projections import project

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


class TestALaterExtractionRevertsAMerge:
    """B34, in the shape that needs no redelivery, no reordering, and no bus.

    The log is in strict order and each event is delivered exactly once. A
    document is extracted, a merge moves one of its edges onto the canonical
    entity, and then the *same document* is re-extracted under a newer model
    -- which `Document.record_extraction` exists to allow, and keys on the
    model version precisely so that a new model can re-run.

    The second `DocumentExtracted` carries the document's edges as extraction
    found them, with their original endpoints. The fold upserts them by id,
    and the merge is silently undone in the read model. Nothing in
    `GraphStore` records that the merge happened, so the fold has nothing to
    consult and no way to notice.

    The real assumption the graph fold makes is therefore not "the bus
    preserves order" but **"no `DocumentExtracted` ever follows a merge that
    touched its entities"** -- which no delivery mechanism can provide,
    because it is a property of what the write side emits.
    """

    async def test_the_merge_is_applied_correctly_before_the_re_extraction(self, tenant_id):
        """The control. Without this, the test below could be passing because
        the merge never worked rather than because it was reverted."""
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

    async def test_a_re_extraction_reverts_the_merge(self, tenant_id):
        """**Asserts the wrong answer.** See this class's docstring.

        When the fold gains an alias representation, this fails with the edge
        pointing at `canonical`. That is the fix landing; delete this test and
        close B34.
        """
        rig = fresh_rig()
        edge, canonical, absorbed, outsider = await build_merge_then_re_extraction(
            rig, tenant_id, re_extract=True
        )

        report = await project(rig.event_store, rig.projections)
        assert report.failed == 0

        edges = (await rig.shape([tenant_id]))[str(tenant_id)]["edges"]
        assert edges[str(edge.id)] == [str(absorbed), str(outsider)], (
            "the re-extraction no longer reverts the merge -- B34 may be fixed"
        )
        assert edges[str(edge.id)] != [str(canonical), str(outsider)], (
            "this is what the edge SHOULD be: the merge moved it onto the "
            "canonical entity, and a later extraction of the same document "
            "must not move it back"
        )

    async def test_replay_reproduces_the_wrong_state_exactly(self, tenant_id):
        """The gap is *deterministic*, not a race.

        Worth pinning separately, because "replay equivalence holds" and "the
        fold is correct" are different claims, and this is the case that
        separates them: wiping and replaying reproduces the reverted merge
        faithfully. A rebuild does not repair it, and no amount of replay
        testing would have found it.
        """
        rig = fresh_rig()
        await build_merge_then_re_extraction(rig, tenant_id, re_extract=True)

        await project(rig.event_store, rig.projections)
        live = await rig.dump([tenant_id])

        await rig.graph_store.delete_by_tenant(tenant_id)
        await rig.vector_store.delete_by_tenant(tenant_id)
        await project(rig.event_store, rig.projections)

        assert await rig.dump([tenant_id]) == live


class TestTheEventStoreIsNotTheProblem:
    """The log is right; only the fold is wrong.

    Stated because the natural first reading of the tests above is that the
    write side emitted something incoherent. It did not: both extractions and
    the merge are events the aggregates legitimately produced, in the order
    they were produced, and the log holds the whole truth. Recovering the
    correct graph from it is possible -- it needs a fold that knows about
    aliases, which is the B34 fix.
    """

    async def test_the_log_holds_both_extractions_and_the_merge(self, tenant_id):
        rig = fresh_rig()
        await build_merge_then_re_extraction(rig, tenant_id, re_extract=True)

        types = [type(envelope.event).__name__ async for envelope in rig.event_store.read_all()]
        assert types == ["DocumentExtracted", "EntitiesMerged", "DocumentExtracted"]
