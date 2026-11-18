"""Contract tests for what an import records when the file states no provenance.

Documented contract (ADR-0151, `composition.import_graph` module docstring):
a provenance field the file states is kept as stated. A field it omits is
filled with no claim of method or certainty: the method becomes
`ExtractionMethod.IMPORTED`, the confidence becomes `IMPORTED_CONFIDENCE`,
and the observation time becomes `imported_at`, or the time of the call.
A relationship without a confidence gets `IMPORTED_CONFIDENCE` too.

The stated confidences below are 0.25 and 0.75, never 0.5 or 1.0, so a
stated value cannot coincide with either the new default or the old one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from redstring.composition.import_graph import (
    IMPORTED_CONFIDENCE,
    import_graph,
    parse_graphml,
    parse_json_ld,
)
from redstring.domain.ids import EntityId, TenantId
from redstring.domain.provenance import ExtractionMethod
from redstring.graph.adapters.memory import InMemoryGraphStore

TENANT = TenantId(UUID("00000000-0000-4000-8000-0000000000aa"))
ADA = "00000000-0000-4000-8000-000000000001"
GRACE = "00000000-0000-4000-8000-000000000002"
EDGE = "00000000-0000-4000-8000-0000000000e1"
#: Far from the old constant default (2026-01-01) and from the test's own clock.
IMPORTED_AT = datetime(2031, 3, 4, 5, 6, 7, tzinfo=UTC)
STATED_AT = datetime(2024, 7, 8, 9, 10, 11, tzinfo=UTC)


def _json_ld(*nodes: dict[str, Any]) -> str:
    return json.dumps({"@context": {}, "@graph": list(nodes)})


def _node(entity_id: str, name: str, **extra: Any) -> dict[str, Any]:
    return {"@id": f"urn:redstring:entity:{entity_id}", "@type": "Person", "name": name, **extra}


def test_the_imported_confidence_claims_nothing_in_either_direction() -> None:
    assert IMPORTED_CONFIDENCE == 0.5


def test_a_node_with_no_provenance_is_marked_imported_at_the_import_time() -> None:
    entities, _ = parse_json_ld(
        _json_ld(_node(ADA, "Ada Lovelace")), tenant_id=TENANT, imported_at=IMPORTED_AT
    )

    provenance = entities[0].provenance
    assert provenance.extraction_method is ExtractionMethod.IMPORTED
    assert provenance.confidence == 0.5
    assert provenance.observed_at == IMPORTED_AT
    assert provenance.source_id is None
    assert provenance.model is None


def test_a_stated_provenance_is_kept_exactly_as_stated() -> None:
    stated = {
        "observedAt": STATED_AT.isoformat(),
        "extractionMethod": "llm",
        "confidence": 0.75,
        "sourceId": "doc-1",
        "model": "fake/canned-v1",
    }
    entities, _ = parse_json_ld(
        _json_ld(_node(ADA, "Ada Lovelace", provenance=stated)),
        tenant_id=TENANT,
        imported_at=IMPORTED_AT,
    )

    provenance = entities[0].provenance
    assert provenance.extraction_method is ExtractionMethod.LLM
    assert provenance.confidence == 0.75
    assert provenance.observed_at == STATED_AT
    assert provenance.source_id == "doc-1"
    assert provenance.model == "fake/canned-v1"


@pytest.mark.parametrize(
    ("stated", "method", "confidence", "observed_at"),
    [
        ({"extractionMethod": "pattern"}, ExtractionMethod.PATTERN, 0.5, IMPORTED_AT),
        ({"confidence": 0.25}, ExtractionMethod.IMPORTED, 0.25, IMPORTED_AT),
        ({"observedAt": STATED_AT.isoformat()}, ExtractionMethod.IMPORTED, 0.5, STATED_AT),
    ],
    ids=["method-only", "confidence-only", "observed-at-only"],
)
def test_each_omitted_field_is_filled_on_its_own(
    stated: dict[str, Any],
    method: ExtractionMethod,
    confidence: float,
    observed_at: datetime,
) -> None:
    entities, _ = parse_json_ld(
        _json_ld(_node(ADA, "Ada Lovelace", provenance=stated)),
        tenant_id=TENANT,
        imported_at=IMPORTED_AT,
    )

    provenance = entities[0].provenance
    assert provenance.extraction_method is method
    assert provenance.confidence == confidence
    assert provenance.observed_at == observed_at


def test_a_json_ld_relationship_without_a_confidence_gets_the_imported_one() -> None:
    edges = [
        {
            "@id": f"urn:redstring:relationship:{EDGE}",
            "@type": "KNEW",
            "target": f"urn:redstring:entity:{GRACE}",
        }
    ]
    _, relationships = parse_json_ld(
        _json_ld(_node(ADA, "Ada Lovelace", relationships=edges), _node(GRACE, "Grace Hopper")),
        tenant_id=TENANT,
    )

    assert [r.confidence for r in relationships] == [0.5]


def _graphml(edge_data: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns"><graph>'
        f'<node id="{ADA}"><data key="d_name">Ada Lovelace</data></node>'
        f'<node id="{GRACE}"><data key="d_name">Grace Hopper</data></node>'
        f'<edge id="{EDGE}" source="{ADA}" target="{GRACE}">{edge_data}</edge>'
        "</graph></graphml>"
    )


def test_a_graphml_node_carries_no_provenance_so_it_is_marked_imported() -> None:
    entities, _ = parse_graphml(_graphml(""), tenant_id=TENANT, imported_at=IMPORTED_AT)

    for entity in entities:
        assert entity.provenance.extraction_method is ExtractionMethod.IMPORTED
        assert entity.provenance.confidence == 0.5
        assert entity.provenance.observed_at == IMPORTED_AT
    assert len(entities) == 2


@pytest.mark.parametrize(
    ("edge_data", "confidence"),
    [("", 0.5), ('<data key="d_conf">0.25</data>', 0.25)],
    ids=["omitted", "stated"],
)
def test_a_graphml_edge_confidence_is_stated_or_imported(edge_data: str, confidence: float) -> None:
    _, relationships = parse_graphml(_graphml(edge_data), tenant_id=TENANT)

    assert [r.confidence for r in relationships] == [confidence]


def test_a_naive_import_time_is_refused() -> None:
    with pytest.raises(ValueError, match="imported_at must be timezone-aware"):
        parse_json_ld(
            _json_ld(_node(ADA, "Ada Lovelace")),
            tenant_id=TENANT,
            imported_at=datetime(2031, 3, 4),
        )


def test_without_an_import_time_the_call_time_is_used() -> None:
    before = datetime.now(UTC)
    entities, _ = parse_json_ld(_json_ld(_node(ADA, "Ada Lovelace")), tenant_id=TENANT)
    after = datetime.now(UTC)

    assert before <= entities[0].provenance.observed_at <= after


def test_every_defaulted_entity_in_one_call_shares_one_import_time() -> None:
    entities, _ = parse_json_ld(
        _json_ld(_node(ADA, "Ada Lovelace"), _node(GRACE, "Grace Hopper")), tenant_id=TENANT
    )

    assert entities[0].provenance.observed_at == entities[1].provenance.observed_at


async def test_import_graph_passes_the_import_time_to_the_store() -> None:
    store = InMemoryGraphStore()

    await import_graph(
        _json_ld(_node(ADA, "Ada Lovelace")), TENANT, graph=store, imported_at=IMPORTED_AT
    )

    stored = await store.get_entity(EntityId(UUID(ADA)), TENANT)
    assert stored is not None
    assert stored.provenance.extraction_method is ExtractionMethod.IMPORTED
    assert stored.provenance.observed_at == IMPORTED_AT
