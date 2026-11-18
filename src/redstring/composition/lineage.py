"""Reverse provenance lineage tracing and corpus coverage reporting."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from redstring.composition.themes.models import DEFAULT_PAGE_SIZE, validate_page_size
from redstring.composition.themes.topology import read_topology
from redstring.domain.normalization import normalize_name

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.alias import Alias
    from redstring.domain.chunk import ChunkId, StoredChunk
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, SourceId, TenantId
    from redstring.domain.provenance import Provenance
    from redstring.domain.relationship import Relationship
    from redstring.ports.chunk_store import ChunkReader
    from redstring.ports.graph_store import AliasStore, EntityReader, RelationshipStore

__all__ = [
    "CorpusHealthReport",
    "EntityLineageReport",
    "SourceLineageReport",
    "analyze_corpus",
    "trace_entity_lineage",
    "trace_source_lineage",
]


@dataclass(frozen=True)
class SourceLineageReport:
    """All graph entities, relationships, and stored chunks traced to a single source document."""

    source_id: SourceId
    tenant_id: TenantId
    entities: tuple[Entity, ...]
    relationships: tuple[Relationship, ...]
    chunks: tuple[StoredChunk, ...]
    elapsed_ms: float

    @property
    def linked_chunk_count(self) -> int:
        """Number of chunks for this source that link to at least one entity."""
        return sum(1 for ch in self.chunks if len(ch.entity_ids) > 0)

    @property
    def unlinked_chunk_count(self) -> int:
        """Number of chunks for this source with no linked entities."""
        return len(self.chunks) - self.linked_chunk_count


@dataclass(frozen=True)
class EntityLineageReport:
    """Complete evidence lineage for a canonical entity."""

    entity: Entity
    tenant_id: TenantId
    source_ids: tuple[SourceId, ...]
    provenance: tuple[Provenance, ...]
    supporting_chunks: tuple[StoredChunk, ...]
    absorbed_aliases: tuple[Alias, ...]
    relationships: tuple[Relationship, ...]
    elapsed_ms: float

    @property
    def aliases(self) -> tuple[Alias, ...]:
        """Alias for ``absorbed_aliases``."""
        return self.absorbed_aliases

    @property
    def chunks(self) -> tuple[StoredChunk, ...]:
        """Alias for ``supporting_chunks``."""
        return self.supporting_chunks


@dataclass(frozen=True)
class CorpusHealthReport:
    """Tenant-wide provenance and chunk coverage health summary."""

    tenant_id: TenantId
    entity_count: int
    relationship_count: int
    unique_source_count: int
    total_chunk_count: int
    embedded_chunk_count: int
    unembedded_chunk_count: int
    linked_chunk_count: int
    unlinked_chunk_count: int
    min_chunk_chars: int
    mean_chunk_chars: float
    max_chunk_chars: int
    entities_with_chunks_count: int
    entities_without_chunks_count: int
    entity_chunk_coverage_ratio: float
    elapsed_ms: float

    @property
    def total_entities(self) -> int:
        return self.entity_count

    @property
    def total_chunks(self) -> int:
        return self.total_chunk_count

    @property
    def linked_chunks(self) -> int:
        return self.linked_chunk_count

    @property
    def unlinked_chunks(self) -> int:
        return self.unlinked_chunk_count

    @property
    def embedded_chunks(self) -> int:
        return self.embedded_chunk_count

    @property
    def unembedded_chunks(self) -> int:
        return self.unembedded_chunk_count


async def trace_source_lineage(
    source_id: SourceId,
    tenant_id: TenantId,
    *,
    graph: EntityReader,
    relationships: RelationshipStore,
    chunks: ChunkReader | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> SourceLineageReport:
    """Trace every entity, relationship, and chunk originating from ``source_id``."""
    validate_page_size(page_size)

    started = time.perf_counter()
    entities_by_id, edges, _ = await read_topology(tenant_id, graph, relationships, page_size)

    matched_entities = tuple(
        sorted(
            (
                entity
                for entity in entities_by_id.values()
                if entity.provenance.source_id == source_id
            ),
            key=lambda e: str(e.id),
        )
    )
    matched_relationships = tuple(
        sorted(
            (edge for edge in edges if edge.source_id == source_id),
            key=lambda r: str(r.id),
        )
    )
    source_chunks: tuple[StoredChunk, ...] = ()
    if chunks is not None:
        found_chunks = await chunks.get_by_source(source_id, tenant_id)
        source_chunks = tuple(found_chunks)

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return SourceLineageReport(
        source_id=source_id,
        tenant_id=tenant_id,
        entities=matched_entities,
        relationships=matched_relationships,
        chunks=source_chunks,
        elapsed_ms=elapsed_ms,
    )


async def trace_entity_lineage(
    entity_id: EntityId | str,
    tenant_id: TenantId,
    *,
    graph: EntityReader,
    relationships: RelationshipStore,
    aliases: AliasStore | None = None,
    chunks: ChunkReader | None = None,
) -> EntityLineageReport | None:
    """Trace full evidence lineage for ``entity_id``, resolving aliases when supplied."""
    started = time.perf_counter()
    if isinstance(entity_id, str):
        matches = await graph.find_entities(tenant_id, name=normalize_name(entity_id), limit=1)
        if not matches:
            return None
        target_id = matches[0].id
    else:
        target_id = entity_id

    canonical_id = target_id
    if aliases is not None:
        resolved_map = await aliases.resolve_entity_ids([target_id], tenant_id)
        canonical_id = resolved_map.get(target_id, target_id)

    entity = await graph.get_entity(canonical_id, tenant_id)
    if entity is None:
        return None

    absorbed: tuple[Alias, ...] = ()
    if aliases is not None:
        found_aliases = await aliases.find_aliases(canonical_id, tenant_id)
        absorbed = tuple(found_aliases)

    incident_rels = await relationships.get_relationships_for([canonical_id], tenant_id)
    sorted_rels = tuple(sorted(incident_rels, key=lambda r: str(r.id)))

    supporting: tuple[StoredChunk, ...] = ()
    if chunks is not None:
        chunk_map: dict[ChunkId, StoredChunk] = {}
        for eid_to_check in [canonical_id, *(a.alias_entity_id for a in absorbed)]:
            for ch in await chunks.get_by_entity(eid_to_check, tenant_id):
                chunk_map[ch.id] = ch
        supporting = tuple(
            sorted(
                chunk_map.values(),
                key=lambda c: (str(c.source_id), c.chunk_index, str(c.id)),
            )
        )

    src_ids: set[SourceId] = set()
    if entity.provenance.source_id is not None:
        src_ids.add(entity.provenance.source_id)
    for rel in sorted_rels:
        if rel.source_id is not None:
            src_ids.add(rel.source_id)
    for ch in supporting:
        src_ids.add(ch.source_id)

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return EntityLineageReport(
        entity=entity,
        tenant_id=tenant_id,
        source_ids=tuple(sorted(src_ids, key=str)),
        provenance=(entity.provenance,),
        supporting_chunks=supporting,
        absorbed_aliases=absorbed,
        relationships=sorted_rels,
        elapsed_ms=elapsed_ms,
    )


async def analyze_corpus(
    tenant_id: TenantId,
    *,
    graph: EntityReader,
    relationships: RelationshipStore,
    chunks: ChunkReader | None = None,
    source_ids: Sequence[SourceId] | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> CorpusHealthReport:
    """Compute provenance and chunk coverage health statistics across ``tenant_id``."""
    validate_page_size(page_size)

    started = time.perf_counter()
    entities_by_id, edges, _ = await read_topology(tenant_id, graph, relationships, page_size)

    discovered_sources: set[SourceId] = set(source_ids or ())
    for entity in entities_by_id.values():
        if entity.provenance.source_id is not None:
            discovered_sources.add(entity.provenance.source_id)
    for edge in edges:
        if edge.source_id is not None:
            discovered_sources.add(edge.source_id)

    all_chunks: dict[ChunkId, StoredChunk] = {}
    entities_with_chunks = 0

    if chunks is not None:
        for eid in entities_by_id:
            entity_chunks = await chunks.get_by_entity(eid, tenant_id)
            if entity_chunks:
                entities_with_chunks += 1
            for ch in entity_chunks:
                all_chunks[ch.id] = ch
                discovered_sources.add(ch.source_id)

        for sid in sorted(discovered_sources, key=str):
            src_chunks = await chunks.get_by_source(sid, tenant_id)
            for ch in src_chunks:
                all_chunks[ch.id] = ch

    entity_count = len(entities_by_id)
    entities_without_chunks = entity_count - entities_with_chunks
    coverage_ratio = (entities_with_chunks / entity_count) if entity_count > 0 else 0.0

    total_chunks = len(all_chunks)
    embedded_chunks = sum(1 for ch in all_chunks.values() if ch.embedding is not None)
    unembedded_chunks = total_chunks - embedded_chunks
    linked_chunks = sum(1 for ch in all_chunks.values() if len(ch.entity_ids) > 0)
    unlinked_chunks = total_chunks - linked_chunks

    if all_chunks:
        lengths = [len(ch.text) for ch in all_chunks.values()]
        min_chars = min(lengths)
        max_chars = max(lengths)
        mean_chars = sum(lengths) / len(lengths)
    else:
        min_chars = 0
        max_chars = 0
        mean_chars = 0.0

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return CorpusHealthReport(
        tenant_id=tenant_id,
        entity_count=entity_count,
        relationship_count=len(edges),
        unique_source_count=len(discovered_sources),
        total_chunk_count=total_chunks,
        embedded_chunk_count=embedded_chunks,
        unembedded_chunk_count=unembedded_chunks,
        linked_chunk_count=linked_chunks,
        unlinked_chunk_count=unlinked_chunks,
        min_chunk_chars=min_chars,
        mean_chunk_chars=mean_chars,
        max_chunk_chars=max_chars,
        entities_with_chunks_count=entities_with_chunks,
        entities_without_chunks_count=entities_without_chunks,
        entity_chunk_coverage_ratio=coverage_ratio,
        elapsed_ms=elapsed_ms,
    )
