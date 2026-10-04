"""Graph topology scanning and degree calculation for theme detection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from redstring.temporal.query import CursorStalledError

from .models import MAX_PAGES

if TYPE_CHECKING:
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, RelationshipId, TenantId
    from redstring.domain.relationship import Relationship
    from redstring.ports.graph_store import EntityReader, RelationshipStore


__all__ = ["node_degrees", "read_topology"]


async def read_topology(
    tenant_id: TenantId,
    graph: EntityReader,
    relationships: RelationshipStore,
    page_size: int,
) -> tuple[dict[EntityId, Entity], list[Relationship], int]:
    """This tenant's whole topology, one page of entities at a time.

    Edges are keyed by `Relationship.id` as they arrive, because an edge whose
    endpoints fall on two different pages is returned by both pages' reads.
    Deduplicating after the fact would be the same thing; doing it here is
    what makes the weight independent of `page_size`.
    """
    entities: dict[EntityId, Entity] = {}
    by_id: dict[RelationshipId, Relationship] = {}
    cursor: EntityId | None = None
    finished = False

    for _ in range(MAX_PAGES):
        page = await graph.find_entities(tenant_id, limit=page_size, after=cursor)
        for entity in page:
            entities[entity.id] = entity
        if page:
            found = await relationships.get_relationships_for(
                [entity.id for entity in page], tenant_id
            )
            for edge in found:
                by_id[edge.id] = edge
        if len(page) < page_size:
            finished = True
            break
        cursor = page[-1].id

    if not finished:
        raise CursorStalledError(tenant_id, MAX_PAGES)

    kept = [
        edge
        for edge in by_id.values()
        if edge.source_entity_id in entities and edge.target_entity_id in entities
    ]
    return entities, kept, len(by_id) - len(kept)


def node_degrees(edges: list[Relationship]) -> dict[EntityId, int]:
    """How many edges touch each entity. A self-loop counts twice."""
    degrees: dict[EntityId, int] = {}
    for edge in edges:
        for endpoint in (edge.source_entity_id, edge.target_entity_id):
            degrees[endpoint] = degrees.get(endpoint, 0) + 1
    return degrees
