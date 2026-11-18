"""Portable knowledge graph import (JSON-LD and GraphML) — logical opposite of `export_graph`.

Parses exported JSON-LD (`@context` + `@graph`) or GraphML XML payloads into
domain `Entity` and `Relationship` objects and idempotently upserts them into
narrow `EntityWriter` and `RelationshipStore` ports.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from uuid import UUID

from redstring.composition.export import ExportFormat
from redstring.domain.blocking import blocking_keys_for
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, RelationshipId, SourceId, TenantId
from redstring.domain.normalization import normalize_name
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker
from redstring.ports.graph_store import RelationshipStore

if TYPE_CHECKING:
    from redstring.ports.graph_store import EntityWriter

__all__ = [
    "GraphImportReport",
    "import_graph",
    "parse_graphml",
    "parse_json_ld",
]

_ENTITY_URN_PREFIX = "urn:redstring:entity:"
_REL_URN_PREFIX = "urn:redstring:relationship:"


@dataclass(frozen=True, slots=True)
class GraphImportReport:
    """Summary of an idempotent portable graph import into a tenant store."""

    entities_imported: int
    relationships_imported: int
    format: ExportFormat
    elapsed_ms: float


def _parse_uuid_urn(value: str, prefix: str, what: str) -> UUID:
    raw = value.removeprefix(prefix) if value.startswith(prefix) else value
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid {what} identifier: {value!r}") from exc


def _parse_provenance(raw_prov: dict[str, Any] | None) -> Provenance:
    if not isinstance(raw_prov, dict):
        return Provenance(
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
        )
    obs_raw = raw_prov.get("observedAt")
    observed_at = (
        datetime.fromisoformat(obs_raw)
        if isinstance(obs_raw, str)
        else datetime(2026, 1, 1, tzinfo=UTC)
    )
    method_raw = raw_prov.get("extractionMethod", "manual")
    method = ExtractionMethod(method_raw)
    confidence = float(raw_prov.get("confidence", 1.0))
    source_id_raw = raw_prov.get("sourceId")
    source_id = SourceId(str(source_id_raw)) if source_id_raw is not None else None
    source_text = raw_prov.get("sourceText")
    model = raw_prov.get("model")
    return Provenance(
        observed_at=observed_at,
        extraction_method=method,
        confidence=confidence,
        source_id=source_id,
        source_text=str(source_text) if source_text is not None else None,
        model=str(model) if model is not None else None,
    )


def _parse_temporal(raw_temp: dict[str, Any] | None) -> TemporalExtent | None:
    if not isinstance(raw_temp, dict) or not raw_temp:
        return None
    start_raw = raw_temp.get("startDate")
    end_raw = raw_temp.get("endDate")
    prec_raw = raw_temp.get("precision")
    unc_raw = raw_temp.get("uncertainty")
    return TemporalExtent(
        start_date=datetime.fromisoformat(start_raw) if isinstance(start_raw, str) else None,
        end_date=datetime.fromisoformat(end_raw) if isinstance(end_raw, str) else None,
        precision=DatePrecision(prec_raw) if isinstance(prec_raw, str) else None,
        uncertainty=UncertaintyMarker(unc_raw) if isinstance(unc_raw, str) else None,
    )


def parse_json_ld(
    payload: str,
    *,
    tenant_id: TenantId,
) -> tuple[list[Entity], list[Relationship]]:
    """Parse a redstring JSON-LD document (`@context` + `@graph`) into domain records."""
    try:
        doc = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON-LD payload: {exc}") from exc

    if not isinstance(doc, dict) or not isinstance(doc.get("@graph"), list):
        raise ValueError("JSON-LD payload must be a JSON object containing a '@graph' list")

    entities: list[Entity] = []
    entity_ids: set[EntityId] = set()
    pending_rels: list[tuple[EntityId, dict[str, Any]]] = []

    for node in doc["@graph"]:
        if not isinstance(node, dict):
            raise ValueError("Every entry in '@graph' must be a JSON object")
        raw_id = node.get("@id")
        name = node.get("name")
        entity_type = node.get("@type")
        if (
            not isinstance(raw_id, str)
            or not isinstance(name, str)
            or not isinstance(entity_type, str)
        ):
            raise ValueError("Entity node in '@graph' requires '@id', '@type', and 'name'")

        eid = EntityId(_parse_uuid_urn(raw_id, _ENTITY_URN_PREFIX, "entity"))
        norm_name = str(node.get("normalizedName") or normalize_name(name))
        description = node.get("description")
        raw_props = node.get("properties")
        props: dict[str, Any] = dict(raw_props) if isinstance(raw_props, dict) else {}
        prov = _parse_provenance(node.get("provenance"))
        temporal = _parse_temporal(node.get("temporal"))

        base_entity = Entity(
            id=eid,
            tenant_id=tenant_id,
            name=name,
            normalized_name=norm_name,
            entity_type=entity_type,
            description=str(description) if description is not None else None,
            properties=props,
            provenance=prov,
            temporal=temporal,
        )
        entity = base_entity.model_copy(update={"blocking_keys": blocking_keys_for(base_entity)})
        entities.append(entity)
        entity_ids.add(eid)

        rels = node.get("relationships", [])
        if isinstance(rels, list):
            for rel_obj in rels:
                if isinstance(rel_obj, dict):
                    pending_rels.append((eid, rel_obj))

    relationships: list[Relationship] = []
    for src_id, rel_obj in pending_rels:
        raw_rel_id = rel_obj.get("@id")
        rel_type = rel_obj.get("@type")
        raw_target = rel_obj.get("target")
        if (
            not isinstance(raw_rel_id, str)
            or not isinstance(rel_type, str)
            or not isinstance(raw_target, str)
        ):
            raise ValueError("Relationship entry requires '@id', '@type', and 'target'")
        rid = RelationshipId(_parse_uuid_urn(raw_rel_id, _REL_URN_PREFIX, "relationship"))
        tgt_id = EntityId(_parse_uuid_urn(raw_target, _ENTITY_URN_PREFIX, "target entity"))
        if tgt_id not in entity_ids:
            raise ValueError(f"Relationship {rid} references unknown target entity {tgt_id}")
        conf = float(rel_obj.get("confidence", 1.0))
        src_doc = rel_obj.get("sourceId")
        raw_rel_props = rel_obj.get("properties")
        rel_props: dict[str, Any] = dict(raw_rel_props) if isinstance(raw_rel_props, dict) else {}
        relationships.append(
            Relationship(
                id=rid,
                tenant_id=tenant_id,
                source_entity_id=src_id,
                target_entity_id=tgt_id,
                relationship_type=rel_type,
                source_id=SourceId(str(src_doc)) if src_doc is not None else None,
                properties=rel_props,
                confidence=conf,
            )
        )

    return entities, relationships


class _GraphMLParser(HTMLParser):
    """Lightweight stdlib parser for GraphML `<node>`, `<edge>`, and `<data>` elements."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.saw_graphml = False
        self.nodes: list[tuple[str, dict[str, str]]] = []
        self.edges: list[tuple[str, str, str, dict[str, str]]] = []
        self._curr_node: tuple[str, dict[str, str]] | None = None
        self._curr_edge: tuple[str, str, str, dict[str, str]] | None = None
        self._curr_key: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k: (v or "") for k, v in attrs}
        local = tag.rsplit(":", 1)[-1]
        if local == "graphml":
            self.saw_graphml = True
        elif local == "node":
            nid = attr_map.get("id", "")
            if not nid:
                raise ValueError("GraphML <node> missing 'id' attribute")
            self._curr_node = (nid, {})
        elif local == "edge":
            eid = attr_map.get("id", "")
            src = attr_map.get("source", "")
            tgt = attr_map.get("target", "")
            if not eid or not src or not tgt:
                raise ValueError("GraphML <edge> requires 'id', 'source', and 'target'")
            self._curr_edge = (eid, src, tgt, {})
        elif local == "data":
            self._curr_key = attr_map.get("key")
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._curr_key is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        local = tag.rsplit(":", 1)[-1]
        if local == "data" and self._curr_key is not None:
            val = "".join(self._buf)
            if self._curr_node is not None:
                self._curr_node[1][self._curr_key] = val
            elif self._curr_edge is not None:
                self._curr_edge[3][self._curr_key] = val
            self._curr_key = None
            self._buf = []
        elif local == "node" and self._curr_node is not None:
            self.nodes.append(self._curr_node)
            self._curr_node = None
        elif local == "edge" and self._curr_edge is not None:
            self.edges.append(self._curr_edge)
            self._curr_edge = None


def parse_graphml(
    payload: str,
    *,
    tenant_id: TenantId,
) -> tuple[list[Entity], list[Relationship]]:
    """Parse a GraphML XML document into domain `Entity` and `Relationship` records."""
    if "<graphml" not in payload or "</graphml>" not in payload:
        raise ValueError("Malformed GraphML payload: missing <graphml> root element")

    parser = _GraphMLParser()
    parser.feed(payload)
    parser.close()

    default_prov = _parse_provenance(None)
    entities: list[Entity] = []
    entity_ids: set[EntityId] = set()

    for raw_id, data in parser.nodes:
        eid = EntityId(_parse_uuid_urn(raw_id, "", "node"))
        name = data.get("d_name", "").strip()
        entity_type = data.get("d_type", "Entity").strip() or "Entity"
        if not name:
            raise ValueError(f"GraphML node {raw_id!r} missing 'd_name' data")
        norm_name = normalize_name(name)
        desc = data.get("d_desc")
        base_entity = Entity(
            id=eid,
            tenant_id=tenant_id,
            name=name,
            normalized_name=norm_name,
            entity_type=entity_type,
            description=desc if desc is not None else None,
            provenance=default_prov,
        )
        entity = base_entity.model_copy(update={"blocking_keys": blocking_keys_for(base_entity)})
        entities.append(entity)
        entity_ids.add(eid)

    relationships: list[Relationship] = []
    for raw_id, raw_src, raw_tgt, data in parser.edges:
        rid = RelationshipId(_parse_uuid_urn(raw_id, "", "edge"))
        src_id = EntityId(_parse_uuid_urn(raw_src, "", "edge source"))
        tgt_id = EntityId(_parse_uuid_urn(raw_tgt, "", "edge target"))
        if src_id not in entity_ids or tgt_id not in entity_ids:
            raise ValueError(f"GraphML edge {rid} references endpoint absent from <node> set")
        rel_type = data.get("d_rel_type", "RELATED_TO").strip() or "RELATED_TO"
        conf = float(data.get("d_conf", "1.0"))
        relationships.append(
            Relationship(
                id=rid,
                tenant_id=tenant_id,
                source_entity_id=src_id,
                target_entity_id=tgt_id,
                relationship_type=rel_type,
                confidence=conf,
            )
        )

    return entities, relationships


async def import_graph(
    payload: str,
    tenant_id: TenantId,
    *,
    graph: EntityWriter,
    relationships: RelationshipStore | None = None,
    format: ExportFormat | str = ExportFormat.JSON_LD,
) -> GraphImportReport:
    """Parse a portable JSON-LD or GraphML payload and idempotently upsert into stores."""
    t0 = time.perf_counter()
    resolved_format = ExportFormat(format)
    if resolved_format is ExportFormat.MERMAID:
        raise ValueError("Mermaid is a visualization-only format and cannot be imported")

    rel_store: RelationshipStore
    if relationships is not None:
        rel_store = relationships
    elif isinstance(graph, RelationshipStore):
        rel_store = graph
    else:
        raise TypeError(
            "relationships must be provided when graph does not implement RelationshipStore"
        )

    if resolved_format is ExportFormat.JSON_LD:
        entities, rels = parse_json_ld(payload, tenant_id=tenant_id)
    else:
        entities, rels = parse_graphml(payload, tenant_id=tenant_id)

    if entities:
        await graph.upsert_entities(entities)
    if rels:
        await rel_store.upsert_relationships(rels)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return GraphImportReport(
        entities_imported=len(entities),
        relationships_imported=len(rels),
        format=resolved_format,
        elapsed_ms=elapsed_ms,
    )
