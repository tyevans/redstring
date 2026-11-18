"""Explainable shortest-path discovery between entities with source passage citations."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from redstring.domain.normalization import normalize_name
from redstring.ports.graph_store import MAX_TRAVERSAL_DEPTH, RelationshipStore

if TYPE_CHECKING:
    from redstring.domain.chunk import ChunkId, StoredChunk
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, TenantId
    from redstring.domain.relationship import Relationship
    from redstring.ports.chunk_store import ChunkReader
    from redstring.ports.graph_store import EntityReader

__all__ = ["PathExplanation", "explain_path"]


@dataclass(frozen=True, slots=True)
class PathExplanation:
    """An explainable multi-hop relationship path connecting two entities."""

    #: Ordered sequence of entities from source to target (length = hops + 1).
    entities: tuple[Entity, ...]
    #: Ordered sequence of relationships connecting consecutive entities (length = hops).
    relationships: tuple[Relationship, ...]
    #: Supporting document chunks citing any entity along the path.
    chunks: tuple[StoredChunk, ...] = ()


async def _resolve_endpoint(
    store: EntityReader,
    tenant_id: TenantId,
    entity_or_name: EntityId | str,
) -> Entity | None:
    if isinstance(entity_or_name, str):
        matches = await store.find_entities(tenant_id, name=normalize_name(entity_or_name), limit=1)
        return matches[0] if matches else None
    return await store.get_entity(entity_or_name, tenant_id)


async def explain_path(
    source: EntityId | str,
    target: EntityId | str,
    *,
    tenant_id: TenantId,
    graph: EntityReader,
    relationships: RelationshipStore | None = None,
    chunks: ChunkReader | None = None,
    max_depth: int = 3,
) -> PathExplanation | None:
    """Find the shortest relationship path between `source` and `target` within `max_depth` hops.

    Traverses relationships bidirectionally via breadth-first search (BFS) with
    deterministic tie-breaking by neighbor `EntityId` and `RelationshipId`.
    When `chunks` is provided, attaches supporting `StoredChunk` passages citing
    the entities along the discovered path.
    """
    if max_depth < 1:
        raise ValueError(f"max_depth must be at least 1, got {max_depth}")
    if max_depth > MAX_TRAVERSAL_DEPTH:
        raise ValueError(f"max_depth must not exceed {MAX_TRAVERSAL_DEPTH}, got {max_depth}")

    rel_store: RelationshipStore
    if relationships is not None:
        rel_store = relationships
    elif isinstance(graph, RelationshipStore):
        rel_store = graph
    else:
        raise TypeError(
            "relationships must be provided when graph does not implement RelationshipStore"
        )

    start_entity = await _resolve_endpoint(graph, tenant_id, source)
    end_entity = await _resolve_endpoint(graph, tenant_id, target)
    if start_entity is None or end_entity is None:
        return None

    if start_entity.id == end_entity.id:
        supporting = await _collect_chunks(chunks, tenant_id, (start_entity,))
        return PathExplanation(
            entities=(start_entity,),
            relationships=(),
            chunks=supporting,
        )

    queue: deque[tuple[tuple[Entity, ...], tuple[Relationship, ...]]] = deque(
        [((start_entity,), ())]
    )
    visited: set[EntityId] = {start_entity.id}

    while queue:
        entity_path, rel_path = queue.popleft()
        current = entity_path[-1]
        if len(rel_path) >= max_depth:
            continue

        rels = await rel_store.get_relationships(current.id, tenant_id)
        # Sort deterministically by neighbor id, relationship_type, and relationship id
        candidates: list[tuple[EntityId, Relationship]] = []
        for rel in rels:
            neighbor_id = (
                rel.target_entity_id if rel.source_entity_id == current.id else rel.source_entity_id
            )
            if neighbor_id not in visited:
                candidates.append((neighbor_id, rel))

        candidates.sort(key=lambda pair: (str(pair[0]), pair[1].relationship_type, str(pair[1].id)))

        for neighbor_id, rel in candidates:
            if neighbor_id in visited:
                continue
            # The target was fetched above, so compare its id before fetching
            # any neighbour. Reaching the target then costs no store read.
            if neighbor_id == end_entity.id:
                neighbor_entity = end_entity
            else:
                fetched = await graph.get_entity(neighbor_id, tenant_id)
                if fetched is None:
                    continue
                neighbor_entity = fetched

            next_entities = (*entity_path, neighbor_entity)
            next_rels = (*rel_path, rel)
            if neighbor_id == end_entity.id:
                supporting = await _collect_chunks(chunks, tenant_id, next_entities)
                return PathExplanation(
                    entities=next_entities,
                    relationships=next_rels,
                    chunks=supporting,
                )

            visited.add(neighbor_id)
            queue.append((next_entities, next_rels))

    return None


async def _collect_chunks(
    chunks: ChunkReader | None,
    tenant_id: TenantId,
    entities: tuple[Entity, ...],
) -> tuple[StoredChunk, ...]:
    if chunks is None:
        return ()
    seen_ids: set[ChunkId] = set()
    collected: list[StoredChunk] = []
    for entity in entities:
        entity_chunks = await chunks.get_by_entity(entity.id, tenant_id)
        for ch in entity_chunks:
            if ch.id not in seen_ids:
                seen_ids.add(ch.id)
                collected.append(ch)
    return tuple(collected)
