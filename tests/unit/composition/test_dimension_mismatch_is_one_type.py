"""Every composition point refuses a mismatched pair with the same type.

B82: `Retriever.__init__` raised `DimensionMismatchError` and `build_graph`
raised `ValueError` for the same condition, and neither `except` catches the
other. Two entry points is a divergence; three is a pattern, and the chunk
retriever is the third. The list is built here rather than asserted per
entry point because a per-entry-point test is what let the first two diverge.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest
from eventsource.application.aggregates.tenant_repository import TenantAwareRepository
from eventsource.ports.snapshots import SnapshotStore
from eventsource.ports.store import AggregateStore

import redstring.composition as composition
from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.composition import ChunkRetriever, Retriever, build_graph
from redstring.consolidation.protocols import CandidateSource, MergeAdjudicator
from redstring.domain.entity import Entity
from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.ids import EntityId, TenantId
from redstring.domain.merge_strategy import PropertyMergePolicy
from redstring.domain.source import SourceDocument
from redstring.events.document import DocumentExtracted
from redstring.events.merge import EntitiesMerged, MergeUndone
from redstring.extraction.domains.models import DomainSchema
from redstring.extraction.protocols import Chunker
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.llm.adapters.fake import FakeLlmProvider
from redstring.llm.adapters.fake_embedding import FakeEmbeddingProvider
from redstring.ports.chunk_store import ChunkStore
from redstring.ports.embedding_provider import EmbeddingProvider
from redstring.ports.graph_store import EntityReader, GraphStore
from redstring.ports.llm_provider import LlmProvider
from redstring.ports.vector_store import VectorReader, VectorStore
from redstring.vector.adapters.memory import InMemoryVectorStore

# `redstring.composition` annotates every collaborator under
# `if TYPE_CHECKING`, so resolving its hints at runtime -- the way
# `tests/unit/graph/test_compliance_coverage.py` resolves `GraphStore`'s --
# needs the names supplied explicitly. This is every name any public
# callable's signature in the module mentions, not a general re-export.
_COMPOSITION_NAMESPACE: dict[str, object] = {
    "Sequence": Sequence,
    "UUID": UUID,
    "EmbeddingProvider": EmbeddingProvider,
    "SourceDocument": SourceDocument,
    "TenantAwareRepository": TenantAwareRepository,
    "SnapshotStore": SnapshotStore,
    "AggregateStore": AggregateStore,
    "CandidateSource": CandidateSource,
    "MergeAdjudicator": MergeAdjudicator,
    "Entity": Entity,
    "EntityId": EntityId,
    "TenantId": TenantId,
    "PropertyMergePolicy": PropertyMergePolicy,
    "DocumentExtracted": DocumentExtracted,
    "EntitiesMerged": EntitiesMerged,
    "MergeUndone": MergeUndone,
    "DomainSchema": DomainSchema,
    "Chunker": Chunker,
    "ChunkRetriever": ChunkRetriever,
    "ChunkStore": ChunkStore,
    "EntityReader": EntityReader,
    "GraphStore": GraphStore,
    "LlmProvider": LlmProvider,
    "VectorReader": VectorReader,
    "VectorStore": VectorStore,
}

TENANT_ID = uuid4()

#: A realistic width, not 8 -- CLAUDE.md records a dimension check written
#: with `is not` that passed at 8 and rejected every legitimate write at 768,
#: because CPython caches small integers.
DIMENSION = 768


def _retriever_mismatch() -> None:
    Retriever(
        embeddings=FakeEmbeddingProvider(dimension=DIMENSION),
        vectors=InMemoryVectorStore(dimension=384),
        graph=InMemoryGraphStore(),
    )


def _build_graph_mismatch() -> None:
    async def _run() -> None:
        empty_answer = {"entities": [], "relationships": []}
        await build_graph(
            SourceDocument(id=f"doc-{uuid4()}", text="Ada Lovelace knows Charles Babbage."),
            provider=FakeLlmProvider(by_substring={}, default=empty_answer),
            store=InMemoryGraphStore(),
            tenant_id=TENANT_ID,
            embedding_provider=FakeEmbeddingProvider(dimension=DIMENSION),
            vector_store=InMemoryVectorStore(dimension=384),
        )

    import asyncio

    asyncio.run(_run())


def _chunk_retriever_mismatch() -> None:
    ChunkRetriever(
        embeddings=FakeEmbeddingProvider(dimension=DIMENSION),
        chunks=InMemoryChunkStore(dimension=384),
    )


CASES = {
    "ChunkRetriever": _chunk_retriever_mismatch,
    "Retriever": _retriever_mismatch,
    "build_graph": _build_graph_mismatch,
}


def _mentions_embedding_provider(annotation: object) -> bool:
    if annotation is EmbeddingProvider:
        return True
    return any(_mentions_embedding_provider(arg) for arg in typing.get_args(annotation))


def _entry_points_taking_an_embedding_provider() -> set[str]:
    """Every public callable/class in `redstring.composition` an `EmbeddingProvider` reaches.

    Walks the module's `__all__`, resolving each name's signature (or, for a
    class, its `__init__`'s signature, walking the MRO the way the public-API
    gate does) with `typing.get_type_hints` so a name under
    `if TYPE_CHECKING` still counts.
    """
    found: set[str] = set()
    for name in composition.__all__:
        obj = getattr(composition, name)
        if inspect.isclass(obj):
            targets = [klass.__init__ for klass in obj.__mro__ if "__init__" in klass.__dict__]
        elif inspect.isfunction(obj):
            targets = [obj]
        else:
            continue

        for target in targets:
            try:
                hints = typing.get_type_hints(target, localns=_COMPOSITION_NAMESPACE)
            except (NameError, TypeError):
                continue
            if any(_mentions_embedding_provider(hint) for hint in hints.values()):
                found.add(name)
                break
    return found


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_dimension_mismatch_is_always_a_dimension_mismatch_error(name: str) -> None:
    with pytest.raises(DimensionMismatchError):
        CASES[name]()


def test_the_case_list_covers_every_composition_point() -> None:
    """Guard the guard: a gate over an empty or stale set passes vacuously.

    Every public callable in `redstring.composition` whose signature names an
    `EmbeddingProvider` must appear in `CASES`, so a fourth entry point fails
    here rather than diverging silently.
    """
    entry_points = _entry_points_taking_an_embedding_provider()
    assert entry_points, "the detector found nothing -- it is broken, not the code"
    assert entry_points <= set(CASES)
