"""Contract tests for `composition.consolidation_sweep` subject resolution, routing and paging.

Documented contract: a subject may be an `Entity`, an `EntityId` or a name; an
id that is not in the graph scans nothing. Candidates are routed by
`decide()` into merge, adjudicate and reject bands. A canonical scan follows
the cursor to the last id of each full page and refuses a cursor that never
advances, naming the tenant and the page bound.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from redstring.composition.consolidation_sweep import preview_consolidation
from redstring.consolidation.candidates import ScoredCandidate
from redstring.consolidation.policy import MergeDecision
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, TenantId
from redstring.domain.normalization import normalize_name
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.similarity import SimilarityFeatures
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.temporal.query import CursorStalledError

TENANT = TenantId(UUID("00000000-0000-4000-8000-0000000000aa"))
PROVENANCE = Provenance(
    observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    extraction_method=ExtractionMethod.MANUAL,
    confidence=1.0,
)


def _eid(n: int) -> EntityId:
    return EntityId(UUID(int=n))


def _entity(n: int, name: str) -> Entity:
    return Entity(
        id=_eid(n),
        tenant_id=TENANT,
        name=name,
        normalized_name=normalize_name(name),
        entity_type="Person",
        provenance=PROVENANCE,
    )


def _candidate(entity: Entity, score: float) -> ScoredCandidate:
    return ScoredCandidate(entity=entity, features=SimilarityFeatures(name=score), score=score)


class _Finder:
    """A `CandidateSource` with scores fixed by the test; it records which subjects it saw."""

    def __init__(self, by_subject: dict[EntityId, list[ScoredCandidate]] | None = None) -> None:
        self.subjects: list[EntityId] = []
        self._by_subject = by_subject or {}

    async def candidates(
        self, subject: Entity, *, minimum_score: float = 0.0
    ) -> list[ScoredCandidate]:
        self.subjects.append(subject.id)
        return list(self._by_subject.get(subject.id, []))


class _PagedGraph:
    """Serves `find_entities` over a sorted list, honouring `after` as the port requires."""

    def __init__(self, entities: list[Entity]) -> None:
        self._entities = sorted(entities, key=lambda e: str(e.id))
        self.after_seen: list[EntityId | None] = []

    async def find_entities(
        self,
        tenant_id: TenantId,
        *,
        limit: int = 50,
        after: EntityId | None = None,
        name: str | None = None,
    ) -> list[Entity]:
        self.after_seen.append(after)
        start = 0
        if after is not None:
            start = next(i for i, e in enumerate(self._entities) if e.id == after) + 1
        return self._entities[start : start + limit]

    async def resolve_entity_ids(
        self, entity_ids: list[EntityId], tenant_id: TenantId
    ) -> dict[EntityId, EntityId]:
        return {}


class _StuckGraph:
    """Returns the same full page forever, as an adapter ignoring `after` would."""

    def __init__(self, entity: Entity) -> None:
        self._entity = entity

    async def find_entities(
        self,
        tenant_id: TenantId,
        *,
        limit: int = 50,
        after: EntityId | None = None,
        name: str | None = None,
    ) -> list[Entity]:
        return [self._entity]

    async def resolve_entity_ids(
        self, entity_ids: list[EntityId], tenant_id: TenantId
    ) -> dict[EntityId, EntityId]:
        return {}


async def test_subject_given_as_entity_is_used_without_a_store_lookup() -> None:
    # The store is empty, so a lookup would find nothing and scan zero entities.
    subject = _entity(1, "Ada Lovelace")
    finder = _Finder()

    preview = await preview_consolidation(
        TENANT,
        graph_store=InMemoryGraphStore(),
        subject=subject,
        finder=finder,
    )

    assert preview.entities_scanned == 1
    assert finder.subjects == [_eid(1)]


async def test_subject_given_as_entity_id_is_looked_up_and_a_missing_one_scans_nothing() -> None:
    store = InMemoryGraphStore()
    await store.upsert_entities([_entity(1, "Ada Lovelace")])

    found_finder = _Finder()
    found = await preview_consolidation(
        TENANT, graph_store=store, subject=_eid(1), finder=found_finder
    )
    assert found.entities_scanned == 1
    assert found_finder.subjects == [_eid(1)]

    missing_finder = _Finder()
    missing = await preview_consolidation(
        TENANT, graph_store=store, subject=_eid(99), finder=missing_finder
    )
    assert missing.entities_scanned == 0
    assert missing_finder.subjects == []


async def test_candidates_are_routed_into_merge_adjudicate_and_reject_bands() -> None:
    subject = _entity(1, "Ada Lovelace")
    merge_twin = _entity(2, "Ada Lovelace")
    adjudicate_twin = _entity(3, "A. Lovelace")
    reject_twin = _entity(4, "Charles Babbage")
    finder = _Finder(
        {
            subject.id: [
                _candidate(reject_twin, 0.10),
                _candidate(merge_twin, 0.95),
                _candidate(adjudicate_twin, 0.80),
            ]
        }
    )

    preview = await preview_consolidation(
        TENANT,
        graph_store=InMemoryGraphStore(),
        subject=subject,
        finder=finder,
    )

    assert [c.candidate.id for c in preview.merge_candidates] == [_eid(2)]
    assert [c.candidate.id for c in preview.adjudicate_candidates] == [_eid(3)]
    assert [c.candidate.id for c in preview.reject_candidates] == [_eid(4)]
    assert preview.reject_candidates[0].decision is MergeDecision.REJECT
    assert preview.rejected_count == 1


async def test_full_pages_advance_the_cursor_to_the_last_id_of_each_page() -> None:
    entities = [_entity(n, f"Person {n}") for n in range(1, 6)]
    graph = _PagedGraph(entities)

    preview = await preview_consolidation(
        TENANT,
        graph_store=graph,  # type: ignore[arg-type]
        finder=_Finder(),
        page_size=2,
    )

    assert preview.entities_scanned == 5
    # Pages [1,2], [3,4], [5]: the third call follows the cursor past page two.
    assert graph.after_seen == [None, _eid(2), _eid(4)]


async def test_cursor_that_never_advances_is_refused_naming_tenant_and_bound() -> None:
    stuck = _StuckGraph(_entity(1, "Ada Lovelace"))

    with pytest.raises(CursorStalledError) as info:
        await preview_consolidation(
            TENANT,
            graph_store=stuck,  # type: ignore[arg-type]
            finder=_Finder(),
            page_size=1,
        )

    message = str(info.value)
    assert str(TENANT) in message
    assert "did not finish in 10000 pages" in message
