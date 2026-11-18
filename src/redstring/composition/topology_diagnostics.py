"""Graph topology diagnostics, hub centrality reporting, and snapshot diffing."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from redstring.composition.themes.models import DEFAULT_PAGE_SIZE
from redstring.composition.themes.topology import read_topology

if TYPE_CHECKING:
    from collections.abc import Mapping

    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, RelationshipId, TenantId
    from redstring.domain.relationship import Relationship
    from redstring.ports.graph_store import EntityReader, RelationshipStore

__all__ = [
    "EntityHub",
    "EntityModification",
    "GraphDiffReport",
    "GraphTopologyReport",
    "RelationshipModification",
    "analyze_topology",
    "diff_graphs",
]


@dataclass(frozen=True)
class EntityHub:
    """Degree centrality summary for a single entity in a tenant graph."""

    entity: Entity
    in_degree: int
    out_degree: int
    total_degree: int

    @property
    def degree(self) -> int:
        """Alias for ``total_degree``."""
        return self.total_degree


@dataclass(frozen=True)
class GraphTopologyReport:
    """Structural diagnostic report for a tenant's knowledge graph."""

    tenant_id: TenantId
    entity_count: int
    relationship_count: int
    dangling_relationship_count: int
    isolated_entity_count: int
    connected_component_count: int
    average_degree: float
    max_degree: int
    entity_type_counts: Mapping[str, int]
    relationship_type_counts: Mapping[str, int]
    top_hubs: tuple[EntityHub, ...]
    elapsed_ms: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "entity_type_counts",
            MappingProxyType(dict(self.entity_type_counts)),
        )
        object.__setattr__(
            self,
            "relationship_type_counts",
            MappingProxyType(dict(self.relationship_type_counts)),
        )

    @property
    def connected_components(self) -> int:
        """Alias for ``connected_component_count``."""
        return self.connected_component_count

    @property
    def isolated_entities(self) -> int:
        """Alias for ``isolated_entity_count``."""
        return self.isolated_entity_count


@dataclass(frozen=True)
class EntityModification:
    """Before/after record for an entity modified between two graph states."""

    entity_id: EntityId
    before: Entity
    after: Entity

    @property
    def changed_fields(self) -> tuple[str, ...]:
        """Tuple of attribute names that differ between ``before`` and ``after``."""
        changed: list[str] = []
        if self.before.name != self.after.name:
            changed.append("name")
        if self.before.entity_type != self.after.entity_type:
            changed.append("entity_type")
        if self.before.description != self.after.description:
            changed.append("description")
        if self.before.external_ids != self.after.external_ids:
            changed.append("external_ids")
        if dict(self.before.properties) != dict(self.after.properties):
            changed.append("properties")
        if self.before.provenance.confidence != self.after.provenance.confidence:
            changed.append("confidence")
        return tuple(changed)


@dataclass(frozen=True)
class RelationshipModification:
    """Before/after record for a relationship modified between two graph states."""

    relationship_id: RelationshipId
    before: Relationship
    after: Relationship

    @property
    def changed_fields(self) -> tuple[str, ...]:
        """Tuple of attribute names that differ between ``before`` and ``after``."""
        changed: list[str] = []
        if self.before.source_entity_id != self.after.source_entity_id:
            changed.append("source_entity_id")
        if self.before.target_entity_id != self.after.target_entity_id:
            changed.append("target_entity_id")
        if self.before.relationship_type != self.after.relationship_type:
            changed.append("relationship_type")
        if self.before.source_id != self.after.source_id:
            changed.append("source_id")
        if self.before.confidence != self.after.confidence:
            changed.append("confidence")
        if dict(self.before.properties) != dict(self.after.properties):
            changed.append("properties")
        return tuple(changed)


@dataclass(frozen=True)
class GraphDiffReport:
    """Structural delta between a base tenant snapshot and a target tenant snapshot."""

    base_tenant_id: TenantId
    target_tenant_id: TenantId
    entities_added: tuple[Entity, ...]
    entities_removed: tuple[Entity, ...]
    entities_modified: tuple[EntityModification, ...]
    relationships_added: tuple[Relationship, ...]
    relationships_removed: tuple[Relationship, ...]
    relationships_modified: tuple[RelationshipModification, ...]
    elapsed_ms: float

    @property
    def is_identical(self) -> bool:
        """Return True when no entities or relationships were added, removed, or modified."""
        return not (
            self.entities_added
            or self.entities_removed
            or self.entities_modified
            or self.relationships_added
            or self.relationships_removed
            or self.relationships_modified
        )


def _count_connected_components(
    entity_ids: list[EntityId],
    edges: list[Relationship],
) -> int:
    if not entity_ids:
        return 0
    parent: dict[EntityId, EntityId] = {eid: eid for eid in entity_ids}

    def find(node: EntityId) -> EntityId:
        root = node
        while parent[root] != root:
            root = parent[root]
        curr = node
        while curr != root:
            nxt = parent[curr]
            parent[curr] = root
            curr = nxt
        return root

    for edge in edges:
        if edge.source_entity_id in parent and edge.target_entity_id in parent:
            root_a = find(edge.source_entity_id)
            root_b = find(edge.target_entity_id)
            if root_a != root_b:
                parent[root_b] = root_a

    return len({find(eid) for eid in entity_ids})


async def analyze_topology(
    tenant_id: TenantId,
    *,
    graph: EntityReader,
    relationships: RelationshipStore,
    top_k_hubs: int = 10,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> GraphTopologyReport:
    """Compute structural topology metrics and hub centrality for ``tenant_id``."""
    if top_k_hubs < 0:
        raise ValueError(f"top_k_hubs must be >= 0, got {top_k_hubs}")
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")

    started = time.perf_counter()
    entities, edges, dangling_count = await read_topology(
        tenant_id, graph, relationships, page_size
    )

    in_deg: Counter[EntityId] = Counter()
    out_deg: Counter[EntityId] = Counter()
    rel_types: Counter[str] = Counter()

    for edge in edges:
        out_deg[edge.source_entity_id] += 1
        in_deg[edge.target_entity_id] += 1
        rel_types[edge.relationship_type] += 1

    entity_types: Counter[str] = Counter()
    hubs: list[EntityHub] = []
    isolated_count = 0
    max_deg = 0
    total_deg_sum = 0

    for eid, entity in entities.items():
        entity_types[entity.entity_type] += 1
        i_d = in_deg.get(eid, 0)
        o_d = out_deg.get(eid, 0)
        t_d = i_d + o_d
        total_deg_sum += t_d
        if t_d == 0:
            isolated_count += 1
        if t_d > max_deg:
            max_deg = t_d
        hubs.append(
            EntityHub(
                entity=entity,
                in_degree=i_d,
                out_degree=o_d,
                total_degree=t_d,
            )
        )

    hubs.sort(key=lambda h: (-h.total_degree, str(h.entity.id)))
    top_hubs = tuple(hubs[:top_k_hubs]) if top_k_hubs > 0 else ()

    entity_count = len(entities)
    avg_degree = (total_deg_sum / entity_count) if entity_count > 0 else 0.0
    component_count = _count_connected_components(list(entities.keys()), edges)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    return GraphTopologyReport(
        tenant_id=tenant_id,
        entity_count=entity_count,
        relationship_count=len(edges),
        dangling_relationship_count=dangling_count,
        isolated_entity_count=isolated_count,
        connected_component_count=component_count,
        average_degree=avg_degree,
        max_degree=max_deg,
        entity_type_counts=dict(sorted(entity_types.items())),
        relationship_type_counts=dict(sorted(rel_types.items())),
        top_hubs=top_hubs,
        elapsed_ms=elapsed_ms,
    )


def _entity_changed(before: Entity, after: Entity) -> bool:
    return (
        before.name != after.name
        or before.entity_type != after.entity_type
        or before.description != after.description
        or before.external_ids != after.external_ids
        or dict(before.properties) != dict(after.properties)
        or before.provenance.confidence != after.provenance.confidence
    )


def _relationship_changed(before: Relationship, after: Relationship) -> bool:
    return (
        before.source_entity_id != after.source_entity_id
        or before.target_entity_id != after.target_entity_id
        or before.relationship_type != after.relationship_type
        or before.source_id != after.source_id
        or before.confidence != after.confidence
        or dict(before.properties) != dict(after.properties)
    )


async def diff_graphs(
    base_tenant_id: TenantId,
    target_tenant_id: TenantId,
    *,
    graph: EntityReader,
    relationships: RelationshipStore,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> GraphDiffReport:
    """Compare two tenant graphs and report added, removed, and modified elements."""
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")

    started = time.perf_counter()
    base_entities, base_edges, _ = await read_topology(
        base_tenant_id, graph, relationships, page_size
    )
    target_entities, target_edges, _ = await read_topology(
        target_tenant_id, graph, relationships, page_size
    )

    base_rel_map = {r.id: r for r in base_edges}
    target_rel_map = {r.id: r for r in target_edges}

    entities_added = tuple(
        sorted(
            (e for eid, e in target_entities.items() if eid not in base_entities),
            key=lambda e: str(e.id),
        )
    )
    entities_removed = tuple(
        sorted(
            (e for eid, e in base_entities.items() if eid not in target_entities),
            key=lambda e: str(e.id),
        )
    )
    entities_modified = tuple(
        sorted(
            (
                EntityModification(
                    entity_id=eid,
                    before=base_entities[eid],
                    after=target_entities[eid],
                )
                for eid in base_entities.keys() & target_entities.keys()
                if _entity_changed(base_entities[eid], target_entities[eid])
            ),
            key=lambda m: str(m.entity_id),
        )
    )

    relationships_added = tuple(
        sorted(
            (r for rid, r in target_rel_map.items() if rid not in base_rel_map),
            key=lambda r: str(r.id),
        )
    )
    relationships_removed = tuple(
        sorted(
            (r for rid, r in base_rel_map.items() if rid not in target_rel_map),
            key=lambda r: str(r.id),
        )
    )
    relationships_modified = tuple(
        sorted(
            (
                RelationshipModification(
                    relationship_id=rid,
                    before=base_rel_map[rid],
                    after=target_rel_map[rid],
                )
                for rid in base_rel_map.keys() & target_rel_map.keys()
                if _relationship_changed(base_rel_map[rid], target_rel_map[rid])
            ),
            key=lambda m: str(m.relationship_id),
        )
    )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return GraphDiffReport(
        base_tenant_id=base_tenant_id,
        target_tenant_id=target_tenant_id,
        entities_added=entities_added,
        entities_removed=entities_removed,
        entities_modified=entities_modified,
        relationships_added=relationships_added,
        relationships_removed=relationships_removed,
        relationships_modified=relationships_modified,
        elapsed_ms=elapsed_ms,
    )
