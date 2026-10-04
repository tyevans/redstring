"""Executable BDD Acceptance Criteria for US-0001: Extract Entities and Relationships.

Governed by:
- ADR-0001 (Event Log Schema and Granularity)
- ADR-0003 (Blackbox Frontdoor Verification)
- ADR-0006 (Gated Public API Surface)
- PRD-0001
- US-0001
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from redstring import (
    FakeLlmProvider,
    InMemoryGraphStore,
    SourceDocument,
    build_graph,
)
from redstring.domain.ids import SourceId


@pytest.mark.asyncio
async def test_extract_entities_and_relations_from_single_document() -> None:
    """Scenario: Extract entities and relations from single document (US-0001)."""
    tenant_id = uuid4()
    store = InMemoryGraphStore()
    source_doc = SourceDocument(
        id=SourceId("doc-1"),
        text="Ada Lovelace collaborated with Charles Babbage on computing engines.",
    )
    answer = {
        "entities": [
            {"name": "Ada Lovelace", "entity_type": "Person"},
            {"name": "Charles Babbage", "entity_type": "Person"},
        ],
        "relationships": [
            {
                "source_name": "Ada Lovelace",
                "target_name": "Charles Babbage",
                "relationship_type": "COLLABORATED_WITH",
            }
        ],
    }
    provider = FakeLlmProvider(by_substring={"Ada": answer})

    report = await build_graph(
        source_doc,
        provider=provider,
        store=store,
        tenant_id=tenant_id,
    )

    assert report.entities == 2
    assert report.relationships == 1

    people = await store.find_entities(tenant_id, entity_type="Person")
    assert len(people) == 2
    names = {p.name for p in people}
    assert "Ada Lovelace" in names
    assert "Charles Babbage" in names


@pytest.mark.asyncio
async def test_handle_document_with_temporal_intervals() -> None:
    """Scenario: Handle document with temporal intervals (US-0001)."""
    tenant_id = uuid4()
    store = InMemoryGraphStore()
    source_doc = SourceDocument(
        id=SourceId("timeline-doc"),
        text="Project Apollo ran from 1961 to 1972.",
    )
    answer = {
        "entities": [
            {
                "name": "Project Apollo",
                "entity_type": "Program",
                "temporal": {"start": "1961-05-25", "end": "1972-12-19"},
            }
        ],
        "relationships": [],
    }
    provider = FakeLlmProvider(by_substring={"Apollo": answer})

    report = await build_graph(
        source_doc,
        provider=provider,
        store=store,
        tenant_id=tenant_id,
    )

    assert report.entities == 1
    programs = await store.find_entities(tenant_id, entity_type="Program")
    assert len(programs) == 1
    assert programs[0].name == "Project Apollo"
