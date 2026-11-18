"""Ontology schema conformance auditing and DomainSchema Mermaid visualization."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from redstring.composition.themes.models import DEFAULT_PAGE_SIZE
from redstring.composition.themes.topology import read_topology
from redstring.extraction.domains.models import DomainSchema, normalize_type_id
from redstring.extraction.domains.registry import get_domain_registry

if TYPE_CHECKING:
    from redstring.domain.ids import TenantId
    from redstring.ports.graph_store import EntityReader, RelationshipStore

__all__ = [
    "SchemaConformanceReport",
    "SchemaViolation",
    "audit_schema_conformance",
    "format_schema_mermaid",
]


@dataclass(frozen=True)
class SchemaViolation:
    """A single ontology conformance violation on a stored entity or relationship."""

    element_kind: Literal["entity", "relationship"]
    element_id: str
    violation_type: Literal[
        "unknown_entity_type",
        "missing_required_property",
        "unknown_relationship_type",
        "invalid_source_type",
        "invalid_target_type",
    ]
    message: str


@dataclass(frozen=True)
class SchemaConformanceReport:
    """Tenant-wide conformance audit against a DomainSchema ontology."""

    tenant_id: TenantId
    domain_id: str
    entities_checked: int
    conforming_entities: int
    relationships_checked: int
    conforming_relationships: int
    conformance_rate: float
    off_schema_entity_types: tuple[str, ...]
    off_schema_relationship_types: tuple[str, ...]
    violations: tuple[SchemaViolation, ...]
    elapsed_ms: float


def _resolve_schema(schema: DomainSchema | str) -> DomainSchema:
    if isinstance(schema, DomainSchema):
        return schema
    return get_domain_registry().get_schema(schema)


def _escape_mermaid_label(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', "#quot;").replace("\n", " ").replace("\r", " ")


def format_schema_mermaid(schema: DomainSchema | str) -> str:
    """Render a ``DomainSchema`` ontology as a Mermaid ``flowchart LR`` diagram."""
    resolved = _resolve_schema(schema)
    lines: list[str] = ["flowchart LR"]

    entity_types = sorted(resolved.entity_types, key=lambda et: et.id)
    all_type_ids = [et.id for et in entity_types]

    for et in entity_types:
        req_props = [p.name for p in et.properties if p.required]
        label = f"{et.id} ({', '.join(req_props)})" if req_props else et.id
        lines.append(f'    type_{et.id}["{_escape_mermaid_label(label)}"]')

    rel_types = sorted(resolved.relationship_types, key=lambda rt: rt.id)
    for rt in rel_types:
        sources = rt.valid_source_types if rt.valid_source_types else all_type_ids
        targets = rt.valid_target_types if rt.valid_target_types else all_type_ids
        arrow = "<-->" if rt.bidirectional else "-->"
        rel_label = _escape_mermaid_label(rt.id)
        for src in sorted(sources):
            for tgt in sorted(targets):
                lines.append(f'    type_{src} {arrow}|"{rel_label}"| type_{tgt}')

    return "\n".join(lines) + "\n"


async def audit_schema_conformance(
    tenant_id: TenantId,
    schema: DomainSchema | str,
    *,
    graph: EntityReader,
    relationships: RelationshipStore,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> SchemaConformanceReport:
    """Audit stored entities and relationships in ``tenant_id`` against ``schema``."""
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")

    resolved = _resolve_schema(schema)
    started = time.perf_counter()
    entities_by_id, edges, _ = await read_topology(tenant_id, graph, relationships, page_size)

    violations: list[SchemaViolation] = []
    off_entity_types: set[str] = set()
    off_rel_types: set[str] = set()
    conforming_entities = 0

    for eid in sorted(entities_by_id.keys(), key=str):
        entity = entities_by_id[eid]
        entity_violations = 0
        et_schema = resolved.get_entity_type(entity.entity_type)
        if et_schema is None:
            off_entity_types.add(entity.entity_type)
            violations.append(
                SchemaViolation(
                    element_kind="entity",
                    element_id=str(entity.id),
                    violation_type="unknown_entity_type",
                    message=(
                        f"Entity '{entity.name}' has type '{entity.entity_type}' "
                        f"which is not declared in domain '{resolved.domain_id}'"
                    ),
                )
            )
            entity_violations += 1
        else:
            norm_props = {normalize_type_id(k): v for k, v in entity.properties.items()}
            for prop_schema in et_schema.properties:
                if prop_schema.required:
                    val = norm_props.get(prop_schema.name)
                    if val is None or val == "":
                        violations.append(
                            SchemaViolation(
                                element_kind="entity",
                                element_id=str(entity.id),
                                violation_type="missing_required_property",
                                message=(
                                    f"Entity '{entity.name}' ({et_schema.id}) is missing "
                                    f"required property '{prop_schema.name}'"
                                ),
                            )
                        )
                        entity_violations += 1
        if entity_violations == 0:
            conforming_entities += 1

    conforming_relationships = 0
    for edge in sorted(edges, key=lambda r: str(r.id)):
        rel_violations = 0
        rt_schema = resolved.get_relationship_type(edge.relationship_type)
        if rt_schema is None:
            off_rel_types.add(edge.relationship_type)
            violations.append(
                SchemaViolation(
                    element_kind="relationship",
                    element_id=str(edge.id),
                    violation_type="unknown_relationship_type",
                    message=(
                        f"Relationship '{edge.id}' has type '{edge.relationship_type}' "
                        f"which is not declared in domain '{resolved.domain_id}'"
                    ),
                )
            )
            rel_violations += 1
        else:
            src_entity = entities_by_id.get(edge.source_entity_id)
            tgt_entity = entities_by_id.get(edge.target_entity_id)
            if src_entity is not None and not rt_schema.is_valid_source(src_entity.entity_type):
                violations.append(
                    SchemaViolation(
                        element_kind="relationship",
                        element_id=str(edge.id),
                        violation_type="invalid_source_type",
                        message=(
                            f"Relationship '{edge.relationship_type}' does not allow "
                            f"source entity type '{src_entity.entity_type}'"
                        ),
                    )
                )
                rel_violations += 1
            if tgt_entity is not None and not rt_schema.is_valid_target(tgt_entity.entity_type):
                violations.append(
                    SchemaViolation(
                        element_kind="relationship",
                        element_id=str(edge.id),
                        violation_type="invalid_target_type",
                        message=(
                            f"Relationship '{edge.relationship_type}' does not allow "
                            f"target entity type '{tgt_entity.entity_type}'"
                        ),
                    )
                )
                rel_violations += 1
        if rel_violations == 0:
            conforming_relationships += 1

    entities_checked = len(entities_by_id)
    relationships_checked = len(edges)
    total_elements = entities_checked + relationships_checked
    conformance_rate = (
        (conforming_entities + conforming_relationships) / total_elements
        if total_elements > 0
        else 1.0
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return SchemaConformanceReport(
        tenant_id=tenant_id,
        domain_id=resolved.domain_id,
        entities_checked=entities_checked,
        conforming_entities=conforming_entities,
        relationships_checked=relationships_checked,
        conforming_relationships=conforming_relationships,
        conformance_rate=conformance_rate,
        off_schema_entity_types=tuple(sorted(off_entity_types)),
        off_schema_relationship_types=tuple(sorted(off_rel_types)),
        violations=tuple(violations),
        elapsed_ms=elapsed_ms,
    )
