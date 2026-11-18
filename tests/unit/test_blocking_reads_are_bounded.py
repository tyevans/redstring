"""Every blocking-key read in the library passes `MAX_BLOCK_READ`.

Documented contract (ADR-0152, `domain.blocking.MAX_BLOCK_READ`):
consolidation and lexical retrieval read at most `MAX_BLOCK_READ` entities
per key. The port cannot enforce that a caller asks for a cap, so this test
asserts it at each caller, through a store that records what it was asked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from redstring.composition.retrieval import Retriever
from redstring.composition.retrieval_diagnostics import explain_entity_retrieval
from redstring.consolidation.candidates import MAX_CANDIDATES, CandidateFinder
from redstring.domain.blocking import MAX_BLOCK_READ, blocking_keys_for
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, TenantId
from redstring.domain.normalization import normalize_name
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.retrieval import RetrievalMode
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.testing.graph_store import EXAMPLE_OBSERVED_AT

if TYPE_CHECKING:
    from collections.abc import Sequence

TENANT = TenantId(UUID("00000000-0000-4000-8000-0000000000aa"))


class _RecordingStore(InMemoryGraphStore):
    """Records the `limit_per_key` of every blocking read."""

    def __init__(self) -> None:
        super().__init__()
        self.limits: list[int | None] = []

    async def find_by_blocking_keys(
        self, keys: Sequence[str], tenant_id: TenantId, *, limit_per_key: int | None = None
    ) -> dict[str, list[Entity]]:
        self.limits.append(limit_per_key)
        return await super().find_by_blocking_keys(keys, tenant_id, limit_per_key=limit_per_key)


def _entity(n: int, name: str) -> Entity:
    built = Entity(
        id=EntityId(UUID(int=n)),
        tenant_id=TENANT,
        name=name,
        normalized_name=normalize_name(name),
        entity_type="Person",
        provenance=Provenance(
            observed_at=EXAMPLE_OBSERVED_AT,
            extraction_method=ExtractionMethod.MANUAL,
            confidence=0.8,
        ),
    )
    return built.model_copy(update={"blocking_keys": blocking_keys_for(built)})


async def _store() -> _RecordingStore:
    store = _RecordingStore()
    await store.upsert_entities([_entity(1, "Ada Lovelace"), _entity(2, "Ada Lovelac")])
    return store


def test_the_candidate_cap_never_exceeds_one_key_read() -> None:
    assert MAX_CANDIDATES <= MAX_BLOCK_READ


async def test_consolidation_caps_its_block_read() -> None:
    store = await _store()

    found = await CandidateFinder(store).candidates(_entity(1, "Ada Lovelace"))

    assert [c.entity.name for c in found] == ["Ada Lovelac"]
    assert store.limits == [MAX_BLOCK_READ]


async def test_lexical_retrieval_caps_its_block_read() -> None:
    store = await _store()

    result = await Retriever.lexical_only(graph=store).retrieve("Ada Lovelace", TENANT, k=5)

    assert result.matches
    assert store.limits == [MAX_BLOCK_READ]


async def test_retrieval_diagnostics_caps_its_block_read() -> None:
    store = await _store()
    await explain_entity_retrieval("Ada Lovelace", TENANT, graph=store, mode=RetrievalMode.LEXICAL)

    assert store.limits == [MAX_BLOCK_READ]
