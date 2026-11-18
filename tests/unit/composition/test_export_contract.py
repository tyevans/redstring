"""Contract tests for `composition.export` JSON-LD fields, centred subgraphs and refusals.

Documented contract: JSON-LD carries every field the entity holds, and omits
the ones it does not hold; `center_entity_id` selects a neighbourhood by id or
by normalised name, and an absent centre yields an empty subgraph; a negative
`max_depth` is refused by name.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from redstring.composition.export import ExportFormat, export_graph, format_json_ld
from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, RelationshipId, SourceId, TenantId
from redstring.domain.normalization import normalize_name
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.domain.temporal import DatePrecision, TemporalExtent, UncertaintyMarker
from redstring.graph.adapters.memory import InMemoryGraphStore

TENANT = TenantId(UUID("00000000-0000-4000-8000-0000000000aa"))
BARE_PROVENANCE = Provenance(
    observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    extraction_method=ExtractionMethod.MANUAL,
    confidence=1.0,
)


def _eid(n: int) -> EntityId:
    return EntityId(UUID(int=n))


def _entity(n: int, name: str, **overrides: Any) -> Entity:
    fields: dict[str, Any] = {
        "id": _eid(n),
        "tenant_id": TENANT,
        "name": name,
        "normalized_name": normalize_name(name),
        "entity_type": "Person",
        "provenance": BARE_PROVENANCE,
    }
    fields.update(overrides)
    return Entity(**fields)


def _nodes_by_id(json_ld: str) -> dict[str, dict[str, Any]]:
    return {n["@id"]: n for n in json.loads(json_ld)["@graph"]}


def test_json_ld_carries_every_provenance_and_temporal_field_the_entity_holds() -> None:
    full = _entity(
        1,
        "Ada Lovelace",
        description="Mathematician",
        properties={"born": 1815},
        provenance=Provenance(
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            extraction_method=ExtractionMethod.LLM,
            confidence=0.9,
            source_id=SourceId("doc-1"),
            source_text="Ada wrote notes.",
            model="fake/canned-v1",
        ),
        temporal=TemporalExtent(
            start_date=datetime(1843, 1, 1, tzinfo=UTC),
            end_date=datetime(1852, 12, 31, tzinfo=UTC),
            precision=DatePrecision.YEAR,
            uncertainty=UncertaintyMarker.CIRCA,
        ),
    )

    node = _nodes_by_id(format_json_ld([full], []))[f"urn:redstring:entity:{_eid(1)}"]

    assert node == {
        "@id": "urn:redstring:entity:00000000-0000-0000-0000-000000000001",
        "@type": "Person",
        "name": "Ada Lovelace",
        "normalizedName": "ada lovelace",
        "description": "Mathematician",
        "properties": {"born": 1815},
        "provenance": {
            "observedAt": "2026-01-01T00:00:00+00:00",
            "extractionMethod": "llm",
            "confidence": 0.9,
            "sourceId": "doc-1",
            "sourceText": "Ada wrote notes.",
            "model": "fake/canned-v1",
        },
        "temporal": {
            "precision": "year",
            "startDate": "1843-01-01T00:00:00+00:00",
            "endDate": "1852-12-31T00:00:00+00:00",
            "uncertainty": "circa",
        },
    }


def test_json_ld_omits_every_optional_field_the_entity_does_not_hold() -> None:
    bare = _entity(2, "Grace Hopper")
    precision_only = _entity(
        3,
        "Charles Babbage",
        temporal=TemporalExtent(precision=DatePrecision.MONTH),
    )

    nodes = _nodes_by_id(format_json_ld([bare, precision_only], []))

    bare_node = nodes[f"urn:redstring:entity:{_eid(2)}"]
    assert bare_node == {
        "@id": "urn:redstring:entity:00000000-0000-0000-0000-000000000002",
        "@type": "Person",
        "name": "Grace Hopper",
        "normalizedName": "grace hopper",
        "provenance": {
            "observedAt": "2026-01-01T00:00:00+00:00",
            "extractionMethod": "manual",
            "confidence": 1.0,
        },
    }
    assert "temporal" not in bare_node
    assert "relationships" not in bare_node

    precision_node = nodes[f"urn:redstring:entity:{_eid(3)}"]
    assert precision_node["temporal"] == {"precision": "month"}


def test_json_ld_keeps_dates_when_the_extent_has_no_precision() -> None:
    # The precision branch must be skipped on its own, not only the date branches.
    undated_precision = _entity(
        4,
        "Edsger Dijkstra",
        temporal=TemporalExtent(
            start_date=datetime(1930, 5, 11, tzinfo=UTC),
            uncertainty=UncertaintyMarker.APPROXIMATE,
        ),
    )

    node = _nodes_by_id(format_json_ld([undated_precision], []))[f"urn:redstring:entity:{_eid(4)}"]

    assert node["temporal"] == {
        "startDate": "1930-05-11T00:00:00+00:00",
        "uncertainty": "approximate",
    }


async def _three_person_graph() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    await store.upsert_entities(
        [
            _entity(1, "Ada Lovelace"),
            _entity(2, "Charles Babbage"),
            _entity(3, "Unrelated Person"),
        ]
    )
    await store.upsert_relationships(
        [
            Relationship(
                id=RelationshipId(UUID(int=0x100)),
                tenant_id=TENANT,
                source_entity_id=_eid(1),
                target_entity_id=_eid(2),
                relationship_type="WORKED_WITH",
                confidence=1.0,
            )
        ]
    )
    return store


async def test_center_given_as_entity_id_selects_that_neighbourhood() -> None:
    store = await _three_person_graph()

    out = await export_graph(
        TENANT,
        graph=store,
        relationships=store,
        format=ExportFormat.JSON_LD,
        center_entity_id=_eid(1),
        max_depth=1,
    )

    names = sorted(n["name"] for n in json.loads(out)["@graph"])
    assert names == ["Ada Lovelace", "Charles Babbage"]


@pytest.mark.parametrize("center", ["No Such Person", None], ids=["name", "missing-id"])
async def test_center_absent_from_the_graph_yields_an_empty_subgraph(center: object) -> None:
    store = await _three_person_graph()
    if center is None:
        center = _eid(99)

    out = await export_graph(
        TENANT,
        graph=store,
        relationships=store,
        format=ExportFormat.JSON_LD,
        center_entity_id=center,  # type: ignore[arg-type]
        max_depth=1,
    )

    assert json.loads(out)["@graph"] == []


async def test_negative_max_depth_is_refused_naming_the_value_when_centred() -> None:
    store = await _three_person_graph()

    with pytest.raises(ValueError, match="max_depth must be non-negative, got -1"):
        await export_graph(
            TENANT,
            graph=store,
            relationships=store,
            format=ExportFormat.JSON_LD,
            center_entity_id=_eid(1),
            max_depth=-1,
        )


async def test_negative_max_depth_is_refused_without_a_centre() -> None:
    """Without a centre `max_depth` has no effect, and a negative one still raises.

    An uncentred export used to ignore the value, so the caller's error stayed
    hidden until a centre was added.
    """
    store = await _three_person_graph()

    with pytest.raises(ValueError, match="max_depth must be non-negative, got -1"):
        await export_graph(
            TENANT,
            graph=store,
            relationships=store,
            format=ExportFormat.JSON_LD,
            max_depth=-1,
        )
