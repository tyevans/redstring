"""Portable knowledge graph import (JSON-LD and GraphML) — logical opposite of `export_graph`.

Parses exported JSON-LD (`@context` + `@graph`) or GraphML XML payloads into
domain `Entity` and `Relationship` objects and idempotently upserts them into
narrow `EntityWriter` and `RelationshipStore` ports.

Trust rule: an import file is trusted for provenance and entity identity.
Provenance is kept as the file states it. A provenance field that the file
does not state is not invented as a confident claim: the method becomes
`ExtractionMethod.IMPORTED`, the confidence becomes `IMPORTED_CONFIDENCE`,
and the observation time becomes the import time. An entity whose id already exists
in the target tenant is replaced, not merged. The parser limits are the only
defence against the file itself: payload size is bounded, JSON nesting that
exceeds the interpreter limit is refused, and GraphML is read by
`composition.graphml_reader`, which refuses DTDs and entity declarations.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from redstring.composition.export import ExportFormat
from redstring.composition.graphml_reader import read_graphml_elements
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
    "IMPORTED_CONFIDENCE",
    "MAX_IMPORT_CHARACTERS",
    "GraphImportReport",
    "import_graph",
    "parse_graphml",
    "parse_json_ld",
]

_ENTITY_URN_PREFIX = "urn:redstring:entity:"
_REL_URN_PREFIX = "urn:redstring:relationship:"

#: Largest JSON-LD or GraphML payload either parser accepts, in characters.
#: The bound is checked before any parsing, so an oversize payload costs no
#: memory beyond the string the caller already holds. It is a generous ceiling
#: for one snapshot, not a figure measured against a workload. A payload above
#: it raises `ValueError`; it is never truncated.
MAX_IMPORT_CHARACTERS: Final = 64 * 1024 * 1024

#: The confidence given to an imported entity or relationship whose file
#: states none. It is the midpoint of the scale: the file made no claim of
#: certainty, so the import makes none in either direction.
IMPORTED_CONFIDENCE: Final = 0.5


def _import_time(imported_at: datetime | None) -> datetime:
    """The observation time for fields the file omits.

    Raises:
        ValueError: `imported_at` has no time zone.
    """
    if imported_at is None:
        return datetime.now(UTC)
    if imported_at.tzinfo is None:
        raise ValueError("imported_at must be timezone-aware")
    return imported_at


def _check_payload_size(payload: str, fmt: str) -> None:
    if len(payload) > MAX_IMPORT_CHARACTERS:
        raise ValueError(
            f"{fmt} payload is {len(payload)} characters; "
            f"the limit is {MAX_IMPORT_CHARACTERS} characters"
        )


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


def _parse_provenance(raw_prov: dict[str, Any] | None, imported_at: datetime) -> Provenance:
    """The file's provenance, with each field it omits marked as imported."""
    if not isinstance(raw_prov, dict):
        raw_prov = {}
    obs_raw = raw_prov.get("observedAt")
    observed_at = datetime.fromisoformat(obs_raw) if isinstance(obs_raw, str) else imported_at
    method_raw = raw_prov.get("extractionMethod")
    method = ExtractionMethod(method_raw) if method_raw is not None else ExtractionMethod.IMPORTED
    confidence = float(raw_prov.get("confidence", IMPORTED_CONFIDENCE))
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
    imported_at: datetime | None = None,
) -> tuple[list[Entity], list[Relationship]]:
    """Parse a redstring JSON-LD document (`@context` + `@graph`) into domain records.

    A provenance field or relationship confidence that the document omits is
    filled as the module docstring states. `imported_at` is the observation
    time used for an omitted `observedAt`; the default is the time of the call.

    Raises `ValueError` for every malformed input, including a payload above
    `MAX_IMPORT_CHARACTERS` and JSON nested past the interpreter's recursion
    limit. Neither `json.JSONDecodeError` nor `RecursionError` escapes.
    """
    _check_payload_size(payload, "JSON-LD")
    import_time = _import_time(imported_at)
    try:
        doc = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON-LD payload: {exc}") from exc
    except RecursionError as exc:
        raise ValueError("JSON-LD payload nests too deeply to parse") from exc

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
        prov = _parse_provenance(node.get("provenance"), import_time)
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
        conf = float(rel_obj.get("confidence", IMPORTED_CONFIDENCE))
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


def parse_graphml(
    payload: str,
    *,
    tenant_id: TenantId,
    imported_at: datetime | None = None,
) -> tuple[list[Entity], list[Relationship]]:
    """Parse a GraphML XML document into domain `Entity` and `Relationship` records.

    GraphML carries no provenance, so every entity is marked as imported, as
    the module docstring states, observed at `imported_at` (default: the time
    of the call). An edge without `d_conf` gets `IMPORTED_CONFIDENCE`.

    Raises `ValueError` for DTDs, entity declarations, malformed XML, child
    markup inside `<data>`, and payloads above `MAX_IMPORT_CHARACTERS`.
    The XML rules are stated in `composition.graphml_reader`.
    """
    _check_payload_size(payload, "GraphML")
    raw_nodes, raw_edges = read_graphml_elements(payload)

    default_prov = _parse_provenance(None, _import_time(imported_at))
    entities: list[Entity] = []
    entity_ids: set[EntityId] = set()

    for raw_id, data in raw_nodes:
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
    for raw_id, raw_src, raw_tgt, data in raw_edges:
        rid = RelationshipId(_parse_uuid_urn(raw_id, "", "edge"))
        src_id = EntityId(_parse_uuid_urn(raw_src, "", "edge source"))
        tgt_id = EntityId(_parse_uuid_urn(raw_tgt, "", "edge target"))
        if src_id not in entity_ids or tgt_id not in entity_ids:
            raise ValueError(f"GraphML edge {rid} references endpoint absent from <node> set")
        rel_type = data.get("d_rel_type", "RELATED_TO").strip() or "RELATED_TO"
        raw_conf = data.get("d_conf")
        conf = float(raw_conf) if raw_conf is not None else IMPORTED_CONFIDENCE
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
    imported_at: datetime | None = None,
) -> GraphImportReport:
    """Parse a portable JSON-LD or GraphML payload and idempotently upsert into stores.

    `imported_at` is the observation time recorded for entities whose file
    states none. The default is the time of the call.
    """
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
        entities, rels = parse_json_ld(payload, tenant_id=tenant_id, imported_at=imported_at)
    else:
        entities, rels = parse_graphml(payload, tenant_id=tenant_id, imported_at=imported_at)

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
