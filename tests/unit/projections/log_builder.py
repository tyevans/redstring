"""Building a representative event log by driving the real aggregates.

Nothing here fabricates an event. Every event in a generated log came out of
`Document` or `ConsolidationLog` and went through a `TenantAwareRepository`,
so a log this builder produces is one the write path could actually have
produced -- including the merges it *refuses*, which is why the builder
catches `ConsolidationInvariantError` and moves on rather than pre-filtering.

The redirections a merge carries are computed here from a mirror of the edge
set, which is what slice 7's consolidation service will compute from the
`GraphStore`. Keeping the mirror is the only real work: a merge has to know
the edges as they are *now*, after earlier merges moved them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, uuid5

from eventsource.domain.tenant_context import tenant_scope
from hypothesis import strategies as st

from redstring.aggregates.repositories import (
    consolidation_repository,
    document_repository,
)
from redstring.domain.chunk import StoredChunk, chunk_id
from redstring.domain.consolidation import RelationshipRedirection
from redstring.domain.entity import Entity
from redstring.domain.exceptions import ConsolidationInvariantError
from redstring.domain.provenance import ExtractionMethod
from redstring.domain.relationship import Relationship
from redstring.domain.vector import VectorRecord
from redstring.events.streams import consolidation_stream, document_stream

from .conftest import DIMENSION, SOURCE_IDS

MODEL = "ollama/qwen3.6-27b"
EMBEDDING_MODEL = "ollama/nomic-embed-text"
CHUNKING_SIGNATURES = ("recursive:abc123", "fixed:def456")

MAX_TENANTS = 2
MAX_DOCUMENTS = len(SOURCE_IDS)
MAX_ENTITIES_PER_DOCUMENT = 4


def _uuid(what: str):
    """A stable id, so hypothesis shrinks toward the same log twice."""
    return uuid5(NAMESPACE_URL, f"https://redstring.test/{what}")


@dataclass(frozen=True)
class DocumentSpec:
    tenant: int
    index: int
    entity_count: int
    edges: tuple[tuple[int, int], ...]
    embedded: bool
    #: How many chunkings this document records: none, one, or a re-chunk on
    #: top of the first. Two is the case that distinguishes a fold using
    #: `replace_source` from one that only upserts -- on a single chunking the
    #: two are the same function.
    chunkings: int = 0


@dataclass(frozen=True)
class Scenario:
    """A log to build, in terms small enough for hypothesis to shrink."""

    tenant_count: int
    documents: tuple[DocumentSpec, ...]
    merges: tuple[tuple[int, int, int], ...] = ()
    undo_positions: tuple[int, ...] = ()

    @property
    def tenant_ids(self):
        return [_uuid(f"tenant/{i}") for i in range(self.tenant_count)]


@st.composite
def scenarios(draw):
    tenant_count = draw(st.integers(min_value=1, max_value=MAX_TENANTS))
    document_count = draw(st.integers(min_value=0, max_value=MAX_DOCUMENTS))

    documents = []
    for index in range(document_count):
        entity_count = draw(st.integers(min_value=0, max_value=MAX_ENTITIES_PER_DOCUMENT))
        raw_edges = draw(
            st.lists(
                st.tuples(
                    st.integers(min_value=0, max_value=MAX_ENTITIES_PER_DOCUMENT - 1),
                    st.integers(min_value=0, max_value=MAX_ENTITIES_PER_DOCUMENT - 1),
                ),
                max_size=4,
            )
        )
        documents.append(
            DocumentSpec(
                tenant=draw(st.integers(min_value=0, max_value=tenant_count - 1)),
                index=index,
                entity_count=entity_count,
                # Self-loops are excluded because `Relationship` forbids them;
                # out-of-range endpoints because the document may hold fewer
                # entities than the strategy's fixed range.
                edges=tuple(
                    (a, b) for a, b in raw_edges if a != b and a < entity_count and b < entity_count
                ),
                embedded=draw(st.booleans()),
                chunkings=draw(st.integers(min_value=0, max_value=2)),
            )
        )

    merges = draw(
        st.lists(
            st.tuples(
                st.integers(min_value=0, max_value=tenant_count - 1),
                st.integers(min_value=0, max_value=7),
                st.integers(min_value=0, max_value=7),
            ),
            max_size=3,
        )
    )
    undo_positions = draw(st.lists(st.integers(min_value=0, max_value=2), max_size=2, unique=True))
    return Scenario(
        tenant_count=tenant_count,
        documents=tuple(documents),
        merges=tuple(merges),
        undo_positions=tuple(undo_positions),
    )


@dataclass
class _TenantMirror:
    """What one tenant's graph should look like once the log is folded.

    This is an **independent oracle**, and the suite needs one. Replay
    equivalence is a self-consistency property: both sides of it run the same
    handlers, so a handler that does too little -- never applies an undo,
    never deletes a dropped edge, never writes relationships at all -- makes
    both sides agree on the same wrong answer and every equivalence test
    passes. Three such mutants survived the equivalence tests before this
    existed.

    The mirror is maintained here, by the builder, from the scenario -- not by
    reading the store and not by folding the events. That is what makes it an
    oracle rather than a second copy of the thing under test.

    **It is independent of the fold, not of `_redirections_for`.** The same
    function computes what a merge displaces *and* feeds the mirror, so a bug
    in that derivation would agree with itself and neither would notice. That
    is acceptable here because the derivation is test scaffolding: nothing in
    `src/` computes redirections yet.

    **Slice 7 must not reuse this as the oracle for its consolidation
    service.** That service is what `_redirections_for` is a stand-in for, and
    checking it against a mirror maintained by the same logic would be
    checking it against itself -- the exact shape that let three handler
    mutants survive the replay-equivalence tests before this oracle existed.
    Slice 7 needs an oracle derived from something it does not own: the
    pre-merge graph read back through the port, or hand-written expectations.
    """

    entity_ids: list = field(default_factory=list)
    edges: dict = field(default_factory=dict)
    vectors: dict = field(default_factory=dict)
    #: source id -> the chunk ids that source should hold, in port order.
    chunks: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BuiltLog:
    """The log that was appended, and what folding it should produce."""

    tenant_ids: list
    expected: dict

    def expected_shape(self) -> dict:
        """The oracle, in the shape `conftest.dump_shape` produces."""
        return {
            str(tenant_id): {
                "entity_ids": sorted(str(i) for i in mirror.entity_ids),
                "edges": {
                    str(edge.id): [
                        str(edge.source_entity_id),
                        str(edge.target_entity_id),
                    ]
                    for edge in mirror.edges.values()
                },
                "vectors": {str(k): v for k, v in mirror.vectors.items()},
                "chunks": dict(mirror.chunks),
            }
            for tenant_id, mirror in self.expected.items()
        }


def _entity(tenant_id, tenant: int, document: int, index: int) -> Entity:
    name = f"entity-{tenant}-{document}-{index}"
    return Entity(
        id=_uuid(f"entity/{tenant}/{document}/{index}"),
        tenant_id=tenant_id,
        name=name,
        normalized_name=name,
        entity_type="thing",
        source_id=f"doc-{document}",
        extraction_method=ExtractionMethod.PATTERN,
        confidence=0.5,
    )


def _chunks(tenant_id, source_id: str, revision: int) -> list[StoredChunk]:
    """Two passages, the second of which changes between revisions.

    One text is carried over on a re-chunk and one is replaced, so the
    expected corpus distinguishes "replaced this source" from both "deleted
    everything" and "kept everything".
    """
    texts = [f"{source_id} opening", f"{source_id} revision {revision}"]
    return [
        StoredChunk(
            id=chunk_id(source_id, text),
            tenant_id=tenant_id,
            source_id=source_id,
            text=text,
            chunk_index=index,
            start_char=0,
            end_char=len(text),
        )
        for index, text in enumerate(texts)
    ]


def _vector(index: int) -> list[float]:
    """Non-zero by construction -- `VectorStore` rejects the zero vector."""
    return [1.0 + index, 0.5, 0.25, 0.125]


async def build_log(event_store, snapshot_store, scenario: Scenario) -> BuiltLog:
    """Append `scenario`'s events, and say what folding them should produce."""
    documents = document_repository(event_store)
    consolidations = consolidation_repository(event_store, snapshot_store)
    tenant_ids = scenario.tenant_ids
    mirrors = {t: _TenantMirror() for t in range(scenario.tenant_count)}

    for spec in scenario.documents:
        tenant_id = tenant_ids[spec.tenant]
        source_id = SOURCE_IDS[spec.index]
        entities = [
            _entity(tenant_id, spec.tenant, spec.index, i) for i in range(spec.entity_count)
        ]
        chunkings = [_chunks(tenant_id, source_id, revision) for revision in range(spec.chunkings)]
        relationships = [
            Relationship(
                id=_uuid(f"rel/{spec.tenant}/{spec.index}/{a}-{b}"),
                tenant_id=tenant_id,
                source_entity_id=entities[a].id,
                target_entity_id=entities[b].id,
                relationship_type="relates_to",
                confidence=0.5,
            )
            for a, b in dict.fromkeys(spec.edges)
        ]

        async with tenant_scope(tenant_id):
            aggregate = await documents.load_or_create(
                document_stream(tenant_id=tenant_id, source_id=source_id).aggregate_id
            )
            aggregate.record_extraction(
                tenant_id=tenant_id,
                source_id=source_id,
                model_version=MODEL,
                entities=entities,
                relationships=relationships,
            )
            if spec.embedded:
                aggregate.record_embeddings(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    embedding_model=EMBEDDING_MODEL,
                    embeddings=[
                        VectorRecord(
                            entity_id=entity.id,
                            tenant_id=tenant_id,
                            vector=_vector(i)[:DIMENSION],
                            metadata={"entity_type": entity.entity_type},
                        )
                        for i, entity in enumerate(entities)
                    ],
                )
            for revision, chunks in enumerate(chunkings):
                aggregate.record_chunking(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    chunking_signature=CHUNKING_SIGNATURES[revision],
                    chunks=chunks,
                )
            await documents.save(aggregate)

        mirror = mirrors[spec.tenant]
        mirror.entity_ids.extend(e.id for e in entities)
        mirror.edges.update({r.id: r for r in relationships})
        if spec.embedded:
            for i, entity in enumerate(entities):
                mirror.vectors[entity.id] = _vector(i)[:DIMENSION]
        if chunkings:
            # Last chunking wins, and the earlier one's unshared passage is an
            # orphan the fold must have deleted -- which is the whole reason
            # `replace_source` is one port method.
            mirror.chunks[source_id] = [chunk.id for chunk in chunkings[-1]]

    merge_event_ids: list = []
    for tenant, canonical_index, absorbed_index in scenario.merges:
        tenant_id = tenant_ids[tenant]
        mirror = mirrors[tenant]
        if not _is_usable_pair(mirror, canonical_index, absorbed_index):
            continue
        canonical = mirror.entity_ids[canonical_index]
        absorbed = mirror.entity_ids[absorbed_index]
        redirections = _redirections_for(mirror, canonical, absorbed)

        async with tenant_scope(tenant_id):
            log = await consolidations.load_or_create(
                consolidation_stream(tenant_id=tenant_id).aggregate_id
            )
            try:
                event = log.merge(
                    tenant_id=tenant_id,
                    canonical_entity_id=canonical,
                    merged_entity_ids=[absorbed],
                    redirections=redirections,
                )
            except ConsolidationInvariantError:
                continue
            await consolidations.save(log)

        _apply_redirections(mirror, redirections)
        merge_event_ids.append((tenant, event.event_id, redirections))

    for position in scenario.undo_positions:
        if position >= len(merge_event_ids):
            continue
        tenant, merge_event_id, redirections = merge_event_ids[position]
        tenant_id = tenant_ids[tenant]
        async with tenant_scope(tenant_id):
            log = await consolidations.load_or_create(
                consolidation_stream(tenant_id=tenant_id).aggregate_id
            )
            try:
                log.undo_merge(tenant_id=tenant_id, merge_event_id=merge_event_id)
            except ConsolidationInvariantError:
                continue
            await consolidations.save(log)
        _restore(mirrors[tenant], redirections)

    return BuiltLog(
        tenant_ids=tenant_ids,
        expected={tenant_ids[t]: mirror for t, mirror in mirrors.items()},
    )


def _is_usable_pair(mirror, canonical_index: int, absorbed_index: int) -> bool:
    return (
        canonical_index != absorbed_index
        and canonical_index < len(mirror.entity_ids)
        and absorbed_index < len(mirror.entity_ids)
    )


def _redirections_for(mirror, canonical, absorbed) -> list[RelationshipRedirection]:
    redirections = []
    for edge in mirror.edges.values():
        if absorbed not in (edge.source_entity_id, edge.target_entity_id):
            continue
        source = canonical if edge.source_entity_id == absorbed else edge.source_entity_id
        target = canonical if edge.target_entity_id == absorbed else edge.target_entity_id
        # Both endpoints absorbed: the edge would become the self-loop
        # `Relationship` forbids, so the merge drops it instead.
        after = (
            None
            if source == target
            else edge.model_copy(update={"source_entity_id": source, "target_entity_id": target})
        )
        redirections.append(RelationshipRedirection(before=edge, after=after))
    return redirections


def _apply_redirections(mirror, redirections) -> None:
    for redirection in redirections:
        if redirection.after is None:
            mirror.edges.pop(redirection.before.id, None)
        else:
            mirror.edges[redirection.before.id] = redirection.after


def _restore(mirror, redirections) -> None:
    for redirection in redirections:
        mirror.edges[redirection.before.id] = redirection.before
