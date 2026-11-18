"""Portable knowledge graph visualization and data export (Mermaid, JSON-LD, GraphML).

Reads a tenant's entities and relationships through `EntityReader` and
`RelationshipStore` and serializes the subgraph into standard, zero-dependency
portable formats:

- `ExportFormat.MERMAID`: Deterministic `flowchart LR` Markdown diagram.
- `ExportFormat.JSON_LD`: Standards-compliant JSON-LD `@context` + `@graph`.
- `ExportFormat.GRAPHML`: GraphML XML document for Gephi, Cytoscape, or NetworkX.
"""

from __future__ import annotations

import json
from collections import deque
from enum import StrEnum
from html import escape as xml_escape
from typing import TYPE_CHECKING, Any

from redstring.composition.themes.models import DEFAULT_PAGE_SIZE
from redstring.composition.themes.topology import read_topology
from redstring.domain.normalization import normalize_name

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, TenantId
    from redstring.domain.relationship import Relationship
    from redstring.ports.graph_store import EntityReader, RelationshipStore

__all__ = [
    "ExportFormat",
    "export_graph",
    "format_graphml",
    "format_json_ld",
    "format_mermaid",
]


class ExportFormat(StrEnum):
    """Supported portable knowledge graph serialization formats."""

    MERMAID = "mermaid"
    JSON_LD = "json-ld"
    GRAPHML = "graphml"


def _node_token(entity_id: object) -> str:
    """Deterministic Mermaid-safe node identifier from an EntityId."""
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(entity_id))
    return f"N_{cleaned}"


def _escape_mermaid(text: str) -> str:
    """Escape characters that break quoted Mermaid labels."""
    return text.replace("\\", "\\\\").replace('"', "'").replace("\n", " ").replace("\r", " ")


def _sort_relationships(
    relationships: Sequence[Relationship],
    entity_ids: set[Any],
) -> list[Relationship]:
    return sorted(
        (
            r
            for r in relationships
            if r.source_entity_id in entity_ids and r.target_entity_id in entity_ids
        ),
        key=lambda r: (
            str(r.source_entity_id),
            str(r.target_entity_id),
            r.relationship_type,
            str(r.id),
        ),
    )


def format_mermaid(
    entities: Sequence[Entity],
    relationships: Sequence[Relationship],
) -> str:
    """Render entities and relationships as a deterministic Mermaid `flowchart LR`."""
    sorted_entities = sorted(entities, key=lambda e: str(e.id))
    entity_ids = {e.id for e in sorted_entities}
    sorted_rels = _sort_relationships(relationships, entity_ids)

    lines: list[str] = ["flowchart LR"]
    for entity in sorted_entities:
        node_id = _node_token(entity.id)
        label = _escape_mermaid(f"{entity.name} ({entity.entity_type})")
        lines.append(f'    {node_id}["{label}"]')

    for rel in sorted_rels:
        src_token = _node_token(rel.source_entity_id)
        tgt_token = _node_token(rel.target_entity_id)
        edge_label = _escape_mermaid(rel.relationship_type)
        lines.append(f'    {src_token} -->|"{edge_label}"| {tgt_token}')

    return "\n".join(lines)


def format_json_ld(
    entities: Sequence[Entity],
    relationships: Sequence[Relationship],
) -> str:
    """Render entities and relationships as a deterministic JSON-LD document."""
    sorted_entities = sorted(entities, key=lambda e: str(e.id))
    entity_ids = {e.id for e in sorted_entities}
    sorted_rels = _sort_relationships(relationships, entity_ids)

    outbound_by_source: dict[Any, list[dict[str, Any]]] = {}
    for rel in sorted_rels:
        rel_entry: dict[str, Any] = {
            "@id": f"urn:redstring:relationship:{rel.id}",
            "@type": rel.relationship_type,
            "target": f"urn:redstring:entity:{rel.target_entity_id}",
            "confidence": rel.confidence,
        }
        if rel.source_id is not None:
            rel_entry["sourceId"] = str(rel.source_id)
        if rel.properties:
            rel_entry["properties"] = dict(sorted(rel.properties.items()))
        outbound_by_source.setdefault(rel.source_entity_id, []).append(rel_entry)

    graph_nodes: list[dict[str, Any]] = []
    for entity in sorted_entities:
        prov_dict: dict[str, Any] = {
            "observedAt": entity.provenance.observed_at.isoformat(),
            "extractionMethod": entity.provenance.extraction_method.value,
            "confidence": entity.provenance.confidence,
        }
        if entity.provenance.source_id is not None:
            prov_dict["sourceId"] = str(entity.provenance.source_id)
        if entity.provenance.source_text is not None:
            prov_dict["sourceText"] = entity.provenance.source_text
        if entity.provenance.model is not None:
            prov_dict["model"] = entity.provenance.model

        node: dict[str, Any] = {
            "@id": f"urn:redstring:entity:{entity.id}",
            "@type": entity.entity_type,
            "name": entity.name,
            "normalizedName": entity.normalized_name,
            "provenance": prov_dict,
        }
        if entity.description is not None:
            node["description"] = entity.description
        if entity.properties:
            node["properties"] = dict(sorted(entity.properties.items()))
        if entity.temporal is not None:
            temporal_dict: dict[str, Any] = {}
            if entity.temporal.precision is not None:
                temporal_dict["precision"] = entity.temporal.precision.value
            if entity.temporal.start_date is not None:
                temporal_dict["startDate"] = entity.temporal.start_date.isoformat()
            if entity.temporal.end_date is not None:
                temporal_dict["endDate"] = entity.temporal.end_date.isoformat()
            if entity.temporal.uncertainty is not None:
                temporal_dict["uncertainty"] = entity.temporal.uncertainty.value
            node["temporal"] = temporal_dict
        if entity.id in outbound_by_source:
            node["relationships"] = outbound_by_source[entity.id]
        graph_nodes.append(node)

    document = {
        "@context": {
            "@vocab": "https://schema.org/",
            "redstring": "https://redstring.dev/vocab#",
            "normalizedName": "redstring:normalizedName",
            "provenance": "redstring:provenance",
            "relationships": "redstring:relationships",
            "temporal": "redstring:temporal",
        },
        "@graph": graph_nodes,
    }
    return json.dumps(document, indent=2, sort_keys=True)


def format_graphml(
    entities: Sequence[Entity],
    relationships: Sequence[Relationship],
) -> str:
    """Render entities and relationships as a GraphML XML string."""
    sorted_entities = sorted(entities, key=lambda e: str(e.id))
    entity_ids = {e.id for e in sorted_entities}
    sorted_rels = _sort_relationships(relationships, entity_ids)

    lines: list[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '  <key id="d_name" for="node" attr.name="name" attr.type="string"/>',
        '  <key id="d_type" for="node" attr.name="entity_type" attr.type="string"/>',
        '  <key id="d_desc" for="node" attr.name="description" attr.type="string"/>',
        '  <key id="d_rel_type" for="edge" attr.name="relationship_type" attr.type="string"/>',
        '  <key id="d_conf" for="edge" attr.name="confidence" attr.type="double"/>',
        '  <graph id="G" edgedefault="directed">',
    ]

    for entity in sorted_entities:
        eid = xml_escape(str(entity.id), quote=True)
        ename = xml_escape(entity.name, quote=True)
        etype = xml_escape(entity.entity_type, quote=True)
        lines.append(f'    <node id="{eid}">')
        lines.append(f'      <data key="d_name">{ename}</data>')
        lines.append(f'      <data key="d_type">{etype}</data>')
        if entity.description is not None:
            edesc = xml_escape(entity.description, quote=True)
            lines.append(f'      <data key="d_desc">{edesc}</data>')
        lines.append("    </node>")

    for rel in sorted_rels:
        rid = xml_escape(str(rel.id), quote=True)
        src = xml_escape(str(rel.source_entity_id), quote=True)
        tgt = xml_escape(str(rel.target_entity_id), quote=True)
        rtype = xml_escape(rel.relationship_type, quote=True)
        lines.append(f'    <edge id="{rid}" source="{src}" target="{tgt}">')
        lines.append(f'      <data key="d_rel_type">{rtype}</data>')
        lines.append(f'      <data key="d_conf">{rel.confidence}</data>')
        lines.append("    </edge>")

    lines.append("  </graph>")
    lines.append("</graphml>")
    return "\n".join(lines)


def _filter_subgraph(
    entities_by_id: dict[EntityId, Entity],
    edges: list[Relationship],
    *,
    entity_types: Sequence[str] | None,
    relationship_types: Sequence[str] | None,
    center_entity_id: EntityId | str | None,
    max_depth: int,
) -> tuple[list[Entity], list[Relationship]]:
    filtered_edges = edges
    if relationship_types is not None:
        allowed_rel_types = set(relationship_types)
        filtered_edges = [e for e in filtered_edges if e.relationship_type in allowed_rel_types]

    allowed_ids = set(entities_by_id.keys())
    if center_entity_id is not None:
        if max_depth < 0:
            raise ValueError(f"max_depth must be non-negative, got {max_depth}")
        center_id: EntityId | None = None
        if isinstance(center_entity_id, str):
            norm = normalize_name(center_entity_id)
            for eid, ent in sorted(entities_by_id.items(), key=lambda kv: str(kv[0])):
                if ent.normalized_name == norm:
                    center_id = eid
                    break
        elif center_entity_id in entities_by_id:
            center_id = center_entity_id

        if center_id is None:
            return [], []

        adj: dict[EntityId, set[EntityId]] = {}
        for edge in filtered_edges:
            adj.setdefault(edge.source_entity_id, set()).add(edge.target_entity_id)
            adj.setdefault(edge.target_entity_id, set()).add(edge.source_entity_id)

        visited: set[EntityId] = {center_id}
        queue: deque[tuple[EntityId, int]] = deque([(center_id, 0)])
        while queue:
            curr, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for nxt in adj.get(curr, ()):
                if nxt not in visited and nxt in entities_by_id:
                    visited.add(nxt)
                    queue.append((nxt, depth + 1))
        allowed_ids &= visited

    if entity_types is not None:
        allowed_ent_types = set(entity_types)
        allowed_ids = {
            eid for eid in allowed_ids if entities_by_id[eid].entity_type in allowed_ent_types
        }

    entity_list = [entities_by_id[eid] for eid in allowed_ids]
    induced_edges = [
        e
        for e in filtered_edges
        if e.source_entity_id in allowed_ids and e.target_entity_id in allowed_ids
    ]
    return entity_list, induced_edges


async def export_graph(
    tenant_id: TenantId,
    *,
    graph: EntityReader,
    relationships: RelationshipStore,
    format: ExportFormat | str = ExportFormat.MERMAID,
    entity_types: Sequence[str] | None = None,
    relationship_types: Sequence[str] | None = None,
    center_entity_id: EntityId | str | None = None,
    max_depth: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> str:
    """Read a tenant's knowledge graph (or filtered subgraph) and serialize it into `format`."""
    if page_size < 1:
        raise ValueError(f"page_size must be at least 1, got {page_size}")

    resolved_format = ExportFormat(format)
    entities_by_id, edges, _ = await read_topology(tenant_id, graph, relationships, page_size)
    entity_list, induced_edges = _filter_subgraph(
        entities_by_id,
        edges,
        entity_types=entity_types,
        relationship_types=relationship_types,
        center_entity_id=center_entity_id,
        max_depth=max_depth,
    )

    if resolved_format is ExportFormat.MERMAID:
        return format_mermaid(entity_list, induced_edges)
    if resolved_format is ExportFormat.JSON_LD:
        return format_json_ld(entity_list, induced_edges)
    return format_graphml(entity_list, induced_edges)
