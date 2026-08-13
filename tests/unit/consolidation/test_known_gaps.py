"""The one B43 consequence that nothing repairs, pinned so it is not forgotten.

**The test in this file asserts that the code is WRONG.** That is deliberate,
and it follows the convention slice 5b established and slice 7 retired when it
closed B34: an assumption written only in a backlog entry is one nobody reads,
and a test that fails the day the gap closes is one whoever closed it must
read.

BACKLOG **B43** describes the window between a merge's graph read and its
stream append. Two of its three consequences repair themselves -- the port's
idempotence covers a redirection for an edge that has gone, and the extraction
fold's alias resolution covers an edge that keeps pointing at an absorbed
entity. The third does not:

**An edge the plan never saw can become a permanent parallel edge, and
re-extraction is what creates it rather than what fixes it.** Deduplication
lives in `plan_redirections`, which by definition never saw that edge; the fold
writes by id, so two distinct ids carrying the same `(source, target, type)`
signature both persist, and nothing later removes either.

That is precisely the state `duplicate_preference` and the whole tie-break
argument exist to prevent. It is deferred rather than fixed because the window
is a store read followed by a stream append on one tenant, consolidation runs
behind extraction, and both candidate fixes have real costs -- see B43. **Only
the re-plan-on-conflict fix addresses this case**; the fold-side fix resolves
the endpoint and leaves the duplicate, which is what already happens here.

If this test starts failing, that is good news. Read B43, work out which fix
landed, delete this file and close the entry.
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
from eventsource.domain.tenant_context import tenant_scope

from redstring.aggregates.repositories import document_repository
from redstring.consolidation.service import ConsolidationService
from redstring.events.streams import document_stream
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.projections import GraphProjection

from .conftest import edge, entity


def _signature(relationship):
    return (
        str(relationship.source_entity_id),
        str(relationship.target_entity_id),
        relationship.relationship_type,
    )


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
        self.documents = document_repository(self.event_store)

    async def extract(self, source_id, entities, relationships, model):
        stream = document_stream(tenant_id=entities[0].tenant_id, source_id=source_id)
        async with tenant_scope(entities[0].tenant_id):
            document = await self.documents.load_or_create(stream.aggregate_id)
            document.record_extraction(
                tenant_id=entities[0].tenant_id,
                source_id=source_id,
                model_version=model,
                entities=entities,
                relationships=relationships,
            )
            await self.documents.save(document)

    async def catch_up(self):
        report = await replay(self.event_store, [self.projection])
        assert report.failed == 0


@pytest.fixture
def tenant_id():
    return uuid4()


async def _merge_blind_to_a_later_edge(rig, tenant_id):
    """Build the window B43 describes, without pretending it is a race.

    The second document is appended to the log and **deliberately not
    projected** before the merge. That is not a contrivance: the graph is a
    projection and lags the log by construction, so "an edge exists in the log
    that the graph read cannot see" is the ordinary state of affairs rather
    than a timing accident. It is what makes B43 a design consequence rather
    than a flake.
    """
    canonical = entity(tenant_id, name="Ada Lovelace")
    absorbed = entity(tenant_id, name="A. Lovelace")
    outsider = entity(tenant_id, name="Analytical Engine")
    already_there = edge(tenant_id, source=canonical.id, target=outsider.id, kind="worked_on")
    await rig.extract("doc-1", [canonical, absorbed, outsider], [already_there], "m1")
    await rig.catch_up()

    # The same claim, from the absorbed entity, in a document the graph has not
    # seen. After the merge these two are the same edge.
    unseen = edge(tenant_id, source=absorbed.id, target=outsider.id, kind="worked_on")
    in_doc_2 = [
        e.model_copy(update={"provenance": e.provenance.model_copy(update={"source_id": "doc-2"})})
        for e in (canonical, absorbed, outsider)
    ]
    await rig.extract("doc-2", in_doc_2, [unseen], "m1")

    merged = await rig.service.merge(
        tenant_id=tenant_id,
        canonical_entity_id=canonical.id,
        merged_entity_ids=[absorbed.id],
    )
    return merged, in_doc_2, unseen, canonical, already_there


class TestAnEdgeTheMergeNeverSawBecomesAPermanentDuplicate:
    async def test_the_plan_cannot_see_it(self):
        """The control, and the premise of everything below. If the plan did
        see the edge it would deduplicate it and there would be no gap."""
        rig, tenant_id = Rig(), uuid4()
        merged, *_ = await _merge_blind_to_a_later_edge(rig, tenant_id)

        assert merged.redirections == []

    async def test_re_extraction_creates_the_duplicate_rather_than_resolving_it(self):
        """**Asserts the wrong answer.** See this module's docstring.

        Before the second document is folded there is one edge on the canonical
        entity. Folding it resolves the endpoint through the alias table --
        correctly, that is B34's fix -- and the result is two edges with the
        same `(source, target, type)` and different ids.
        """
        rig, tenant_id = Rig(), uuid4()
        _merged, in_doc_2, unseen, canonical, _existing = await _merge_blind_to_a_later_edge(
            rig, tenant_id
        )
        await rig.catch_up()

        before = await rig.graph_store.get_relationships(canonical.id, tenant_id)
        assert len(before) == 1, "the unseen edge is still on the absorbed entity"

        await rig.extract("doc-2", in_doc_2, [unseen], "m2")
        await rig.catch_up()

        after = await rig.graph_store.get_relationships(canonical.id, tenant_id)
        signatures = [_signature(relationship) for relationship in after]
        assert len(after) == 2, (
            "the re-extraction no longer produces a second edge -- B43 may be fixed"
        )
        assert len(set(signatures)) == 1, "the two edges no longer make the same claim -- check B43"

    async def test_nothing_later_removes_it(self):
        """Not merely "it appears" but "it stays". A gap that a subsequent
        merge tidied up would be a latency problem rather than a correctness
        one, and B43 would not need the re-plan fix."""
        rig, tenant_id = Rig(), uuid4()
        _merged, in_doc_2, unseen, canonical, _existing = await _merge_blind_to_a_later_edge(
            rig, tenant_id
        )
        await rig.catch_up()
        await rig.extract("doc-2", in_doc_2, [unseen], "m2")
        await rig.catch_up()

        # A third extraction, and a whole replay of the log from scratch.
        await rig.extract("doc-2", in_doc_2, [unseen], "m3")
        await rig.catch_up()
        await rig.graph_store.delete_by_tenant(tenant_id)
        await replay(
            rig.event_store,
            [
                GraphProjection(
                    rig.graph_store,
                    checkpoint_repo=InMemoryCheckpointRepository(),
                    dlq_repo=InMemoryDLQRepository(),
                )
            ],
        )

        after = await rig.graph_store.get_relationships(canonical.id, tenant_id)
        assert len(after) == 2, (
            "a replay no longer reproduces the parallel edge -- B43 may be fixed"
        )
