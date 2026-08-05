"""Shared builders for the consolidation suite. Everything here is real."""

from __future__ import annotations

from uuid import uuid4

from redstring.domain.entity import Entity, ExtractionMethod
from redstring.domain.relationship import Relationship


def entity(
    tenant_id, *, name="Ada Lovelace", entity_id=None, source_id="doc-1", **overrides
) -> Entity:
    """`source_id` defaults to "doc-1" because `DocumentExtracted` requires every
    entity to be attributed to the document it came from -- a `None` there is
    refused by the event, not by `Entity`."""
    fields = {
        "id": entity_id or uuid4(),
        "tenant_id": tenant_id,
        "source_id": source_id,
        "name": name,
        "normalized_name": name.lower(),
        "entity_type": "person",
        "extraction_method": ExtractionMethod.MANUAL,
        "confidence": 1.0,
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
