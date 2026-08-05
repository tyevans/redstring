"""The `Relationship` domain type: an edge between two entities."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator, model_validator

from redstring.domain.ids import EntityId, RelationshipId, TenantId


class Relationship(BaseModel):
    """A directed, typed edge between two entities."""

    id: RelationshipId
    tenant_id: TenantId
    source_entity_id: EntityId
    target_entity_id: EntityId
    relationship_type: str
    properties: dict[str, Any] = {}
    confidence: float

    @field_validator("confidence")
    @classmethod
    def _require_confidence_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value

    @model_validator(mode="after")
    def _reject_self_loops(self) -> Relationship:
        if self.source_entity_id == self.target_entity_id:
            raise ValueError("source_entity_id and target_entity_id must differ")
        return self
