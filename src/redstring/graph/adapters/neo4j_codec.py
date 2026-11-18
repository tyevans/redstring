"""How the Neo4j adapter encodes domain types as node and edge properties.

Moved out of `neo4j.py` so the adapter module holds the Cypher and this one
holds the pure conversions. Nothing here talks to a database, and no Cypher
may appear here: `tests/unit/graph/test_neo4j_adapter_is_wired.py` fails on
Cypher outside `neo4j.py`. The adapter imports every name below, so
`neo4j._entity_from` and the rest still resolve there.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from redstring.domain.alias import Alias
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, RelationshipId, TenantId
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.domain.temporal import TemporalExtent

if TYPE_CHECKING:
    from collections.abc import Sequence

    from neo4j.graph import Node
    from neo4j.graph import Relationship as Edge

__all__ = ["rows_carrying_keys"]


# ----------------------------------------------------------------------
# Encoding
#
# A Neo4j property is a primitive or a homogeneous array. Everything that is
# neither becomes JSON text; everything the port queries on stays native.
# ----------------------------------------------------------------------


def _entity_row(entity: Entity) -> dict[str, Any]:
    return {
        "tenant_id": str(entity.tenant_id),
        "id": str(entity.id),
        "name": entity.name,
        "normalized_name": entity.normalized_name,
        "entity_type": entity.entity_type,
        "original_entity_type": entity.original_entity_type,
        "description": entity.description,
        # Provenance is flattened onto the node rather than stored as one JSON
        # blob: `extraction_method` and `model` are what "re-extract
        # everything the old model touched" queries on, and a blob is not
        # queryable in Cypher. Every key is written on every upsert because
        # `SET e = row` replaces the whole property set -- an omitted key is
        # not left alone, it is erased.
        "source_id": entity.provenance.source_id,
        "source_text": entity.provenance.source_text,
        "extraction_method": entity.provenance.extraction_method.value,
        "model": entity.provenance.model,
        "confidence": entity.provenance.confidence,
        # ISO text rather than a native `DateTime`, for the reason `_alias_row`
        # gives about `merged_at`: the driver's round-trip is lossy for offsets
        # Python spells differently, and the port compares entities for
        # equality.
        "observed_at": entity.provenance.observed_at.isoformat(),
        # A list, so `find_by_blocking_key` can filter on it in Cypher. Sorted
        # for a stable stored form; the domain type is a set either way.
        # `None` is left as null (Neo4j drops the property) while an empty set
        # is stored as an empty array, which keeps "no keys known" and "known
        # to have none" distinguishable on the way back.
        "blocking_keys": None if entity.blocking_keys is None else sorted(entity.blocking_keys),
        "external_ids_json": json.dumps(entity.external_ids),
        "properties_json": json.dumps(entity.properties),
        "temporal_json": None if entity.temporal is None else entity.temporal.model_dump_json(),
    }


def _entity_from(node: Node) -> Entity:
    temporal = node.get("temporal_json")
    return Entity(
        id=EntityId(UUID(node["id"])),
        tenant_id=TenantId(UUID(node["tenant_id"])),
        name=node["name"],
        normalized_name=node["normalized_name"],
        entity_type=node["entity_type"],
        original_entity_type=node.get("original_entity_type"),
        description=node.get("description"),
        external_ids=json.loads(node["external_ids_json"]),
        properties=json.loads(node["properties_json"]),
        provenance=Provenance(
            observed_at=datetime.fromisoformat(node["observed_at"]),
            extraction_method=ExtractionMethod(node["extraction_method"]),
            confidence=node["confidence"],
            # `.get`, because Neo4j drops a property written as null.
            source_id=node.get("source_id"),
            source_text=node.get("source_text"),
            model=node.get("model"),
        ),
        temporal=None if temporal is None else TemporalExtent.model_validate_json(temporal),
        blocking_keys=(
            None if node.get("blocking_keys") is None else frozenset(node["blocking_keys"])
        ),
    )


def rows_carrying_keys(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows `_write_blocking_keys` has anything to create edges for.

    Public and pure so the default gate can test it: everything around it is
    Cypher and only runs when a server is up, and "which rows need the second
    statement" is the one decision in that method rather than plumbing.

    Truthiness, deliberately -- it must drop both `None` ("no keys known") and
    `[]` ("known to have none"). The two differ to `_entity_from`, which is why
    the property keeps them apart, but not here: neither creates an edge.
    """
    return [row for row in rows if row["blocking_keys"]]


def _alias_row(alias: Alias) -> dict[str, Any]:
    """Alias properties for the `:ALIAS_OF` edge.

    `merged_at` is ISO text rather than a native Neo4j `DateTime`, so the
    timezone survives verbatim: the driver returns a `neo4j.time.DateTime`
    whose conversion back is lossy for offsets Python spells differently, and
    the port compares `Alias`es for equality.
    """
    return {
        "id": str(alias.id),
        "tenant_id": str(alias.tenant_id),
        "canonical_entity_id": str(alias.canonical_entity_id),
        "alias_entity_id": str(alias.alias_entity_id),
        "alias_name": alias.alias_name,
        "alias_normalized_name": alias.alias_normalized_name,
        "merged_at": alias.merged_at.isoformat(),
        "merge_reason": alias.merge_reason,
    }


def _alias_from(edge: Edge) -> Alias:
    return Alias(
        id=UUID(edge["id"]),
        tenant_id=TenantId(UUID(edge["tenant_id"])),
        canonical_entity_id=EntityId(UUID(edge["canonical_entity_id"])),
        alias_entity_id=EntityId(UUID(edge["alias_entity_id"])),
        # `.get`, because Neo4j drops a property written as null and both
        # names are legitimately absent -- the fold writes an alias with no
        # name when the absorbed entity's extraction has not been folded yet.
        alias_name=edge.get("alias_name"),
        alias_normalized_name=edge.get("alias_normalized_name"),
        merged_at=datetime.fromisoformat(edge["merged_at"]),
        merge_reason=edge.get("merge_reason"),
    )


def _relationship_row(relationship: Relationship) -> dict[str, Any]:
    return {
        "tenant_id": str(relationship.tenant_id),
        "id": str(relationship.id),
        # Redundant against the edge's own endpoints, and kept because it lets
        # a read decode a relationship from the edge alone -- no second match
        # for the nodes just to learn which way round it goes.
        "source_entity_id": str(relationship.source_entity_id),
        "target_entity_id": str(relationship.target_entity_id),
        "relationship_type": relationship.relationship_type,
        "source_id": relationship.source_id,
        "properties_json": json.dumps(relationship.properties),
        "confidence": relationship.confidence,
    }


def _relationship_from(edge: Edge) -> Relationship:
    return Relationship(
        id=RelationshipId(UUID(edge["id"])),
        tenant_id=TenantId(UUID(edge["tenant_id"])),
        source_entity_id=EntityId(UUID(edge["source_entity_id"])),
        target_entity_id=EntityId(UUID(edge["target_entity_id"])),
        relationship_type=edge["relationship_type"],
        # `.get`, not `[]`: Neo4j drops a property written as null, and an
        # edge written before this field existed has none either.
        source_id=edge.get("source_id"),
        properties=json.loads(edge["properties_json"]),
        confidence=edge["confidence"],
    )
