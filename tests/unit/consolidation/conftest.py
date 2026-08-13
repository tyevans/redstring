"""Shared builders for the consolidation suite. Everything here is real."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from redstring.domain.entity import Entity
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship

#: Fixed rather than `datetime.now(UTC)`: a fixture that varies per run makes
#: any comparison on `observed_at` non-deterministic, and this suite compares
#: entities for equality.
OBSERVED = datetime(2026, 1, 15, 8, 20, tzinfo=UTC)


def entity(
    tenant_id,
    *,
    name="Ada Lovelace",
    entity_id=None,
    source_id="doc-1",
    confidence=1.0,
    observed_at=OBSERVED,
    **overrides,
) -> Entity:
    """`source_id` defaults to "doc-1" because `DocumentExtracted` requires every
    entity to be attributed to the document it came from -- a `None` there is
    refused by the event, not by `Entity`."""
    fields = {
        "id": entity_id or uuid4(),
        "tenant_id": tenant_id,
        "name": name,
        "normalized_name": name.lower(),
        "entity_type": "person",
        "provenance": Provenance(
            observed_at=observed_at,
            extraction_method=ExtractionMethod.MANUAL,
            confidence=confidence,
            source_id=source_id,
        ),
    }
    fields.update(overrides)
    return Entity(**fields)


def edge(tenant_id, *, source, target, kind="knows", confidence=0.5, **overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "source_entity_id": source,
        "target_entity_id": target,
        "relationship_type": kind,
        "confidence": confidence,
    }
    fields.update(overrides)
    return Relationship(**fields)
