"""Contract tests for the refusal paths in `composition.import_graph`.

Documented contract: every malformed JSON-LD or GraphML payload raises
`ValueError`; Mermaid is visualization-only and cannot be imported; a writer
that is not also a `RelationshipStore` needs `relationships=` supplied.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from redstring.composition.import_graph import import_graph, parse_graphml, parse_json_ld
from redstring.domain.ids import EntityId, RelationshipId, TenantId
from redstring.graph.adapters.memory import InMemoryGraphStore

if TYPE_CHECKING:
    from redstring.domain.entity import Entity
    from redstring.domain.relationship import Relationship

TENANT = TenantId(UUID("00000000-0000-4000-8000-0000000000aa"))
ADA = "00000000-0000-4000-8000-000000000001"
GRACE = "00000000-0000-4000-8000-000000000002"
EDGE = "00000000-0000-4000-8000-0000000000e1"


def _json_ld(graph: list[Any]) -> str:
    return json.dumps({"@context": {}, "@graph": graph})


def _node(
    entity_id: str = ADA,
    *,
    name: str | None = "Ada Lovelace",
    entity_type: str | None = "Person",
    relationships: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {"@id": f"urn:redstring:entity:{entity_id}"}
    if name is not None:
        node["name"] = name
    if entity_type is not None:
        node["@type"] = entity_type
    if relationships is not None:
        node["relationships"] = relationships
    return node


# --- JSON-LD ---------------------------------------------------------------


def test_json_ld_entity_identifier_that_is_not_a_uuid_is_refused_naming_it() -> None:
    bad = "urn:redstring:entity:not-a-uuid"
    payload = _json_ld([_node(entity_id="not-a-uuid")])

    with pytest.raises(ValueError, match=re.escape(f"Invalid entity identifier: {bad!r}")):
        parse_json_ld(payload, tenant_id=TENANT)


def test_malformed_json_is_refused() -> None:
    with pytest.raises(ValueError, match="Malformed JSON-LD payload"):
        parse_json_ld("{not json", tenant_id=TENANT)


def test_document_without_graph_list_is_refused() -> None:
    with pytest.raises(ValueError, match="must be a JSON object containing a '@graph' list"):
        parse_json_ld('{"@graph": "not a list"}', tenant_id=TENANT)


def test_graph_entry_that_is_not_an_object_is_refused_after_a_valid_entry() -> None:
    payload = _json_ld([_node(), 42])

    with pytest.raises(ValueError, match="Every entry in '@graph' must be a JSON object"):
        parse_json_ld(payload, tenant_id=TENANT)


def test_entity_without_name_is_refused_after_a_valid_entity() -> None:
    payload = _json_ld([_node(), _node(GRACE, name=None)])

    with pytest.raises(ValueError, match="requires '@id', '@type', and 'name'"):
        parse_json_ld(payload, tenant_id=TENANT)


def test_relationship_without_target_is_refused_after_a_valid_relationship() -> None:
    good_rel = {
        "@id": f"urn:redstring:relationship:{EDGE}",
        "@type": "KNOWS",
        "target": f"urn:redstring:entity:{GRACE}",
    }
    bad_rel = {"@id": f"urn:redstring:relationship:{EDGE}", "@type": "KNOWS"}
    payload = _json_ld(
        [
            _node(relationships=[good_rel, bad_rel]),
            _node(GRACE),
        ]
    )

    with pytest.raises(
        ValueError, match="Relationship entry requires '@id', '@type', and 'target'"
    ):
        parse_json_ld(payload, tenant_id=TENANT)


# --- GraphML ---------------------------------------------------------------

_GRAPHML_OPEN = '<graphml xmlns="http://graphml.graphdrawing.org/xmlns"><graph id="G">'
_GRAPHML_CLOSE = "</graph></graphml>"


def test_graphml_node_without_name_data_is_refused_naming_the_node() -> None:
    payload = (
        _GRAPHML_OPEN + f'<node id="{ADA}"><data key="d_type">Person</data></node>' + _GRAPHML_CLOSE
    )

    with pytest.raises(ValueError, match=re.escape(f"GraphML node '{ADA}' missing 'd_name' data")):
        parse_graphml(payload, tenant_id=TENANT)


def test_graphml_edge_with_absent_endpoint_is_refused_naming_the_edge() -> None:
    payload = (
        _GRAPHML_OPEN
        + f'<node id="{ADA}"><data key="d_name">Ada</data></node>'
        + f'<edge id="{EDGE}" source="{ADA}" target="{GRACE}"/>'
        + _GRAPHML_CLOSE
    )

    with pytest.raises(
        ValueError,
        match=re.escape(f"GraphML edge {RelationshipId(UUID(EDGE))} references endpoint absent"),
    ):
        parse_graphml(payload, tenant_id=TENANT)


# --- import_graph entry point ---------------------------------------------


async def test_mermaid_is_refused_as_an_import_format() -> None:
    store = InMemoryGraphStore()

    with pytest.raises(ValueError, match="Mermaid is a visualization-only format"):
        await import_graph("flowchart LR", TENANT, graph=store, format="mermaid")


class _RelationshipRecorder:
    """A `RelationshipStore` stand-in that records what it is given."""

    def __init__(self) -> None:
        self.relationships: list[Relationship] = []

    async def upsert_relationships(self, relationships: list[Relationship]) -> None:
        self.relationships.extend(relationships)


class _EntityWriterOnly:
    """An `EntityWriter` with no relationship capability."""

    def __init__(self) -> None:
        self.entities: list[Entity] = []

    async def upsert_entities(self, entities: list[Entity]) -> None:
        self.entities.extend(entities)


async def test_writer_without_relationship_capability_needs_a_relationship_store() -> None:
    with pytest.raises(
        TypeError,
        match="relationships must be provided when graph does not implement RelationshipStore",
    ):
        await import_graph(_json_ld([]), TENANT, graph=_EntityWriterOnly())  # type: ignore[arg-type]


async def test_explicit_relationship_store_receives_edges_from_a_plain_writer() -> None:
    writer = _EntityWriterOnly()
    rel_store = _RelationshipRecorder()
    payload = _json_ld(
        [
            _node(
                relationships=[
                    {
                        "@id": f"urn:redstring:relationship:{EDGE}",
                        "@type": "KNOWS",
                        "target": f"urn:redstring:entity:{GRACE}",
                    }
                ]
            ),
            _node(GRACE, name="Grace Hopper"),
        ]
    )

    report = await import_graph(
        payload,
        TENANT,
        graph=writer,  # type: ignore[arg-type]
        relationships=rel_store,
    )

    assert report.entities_imported == 2
    assert report.relationships_imported == 1
    assert [e.id for e in writer.entities] == [EntityId(UUID(ADA)), EntityId(UUID(GRACE))]
    assert [str(r.id) for r in rel_store.relationships] == [EDGE]
