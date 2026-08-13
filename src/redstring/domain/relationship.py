"""The `Relationship` domain type: an edge between two entities."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator, model_validator

from redstring.domain.ids import EntityId, RelationshipId, SourceId, TenantId
from redstring.domain.json_safety import Passthrough, reject_unstorable_text


class Relationship(BaseModel):
    """A directed, typed edge between two entities."""

    id: RelationshipId
    tenant_id: TenantId
    source_entity_id: EntityId
    target_entity_id: EntityId
    relationship_type: str
    #: Which document stated this edge, matching `Entity.provenance.source_id`.
    #:
    #: Optional for the reason `Entity`'s is: this reaches the event log, and
    #: an event written before the field existed replays without it. `None`
    #: therefore means "not recorded", never "no document".
    #:
    #: There is deliberately no `source_text` beside it. `Entity` has one
    #: because the extraction schema asks the model for it;
    #: `ExtractedRelationship` has no span field, so a `source_text` here
    #: could only be reconstructed or paraphrased -- and a paraphrase in a
    #: field named for a quotation reads as evidence while being generation.
    #: See BACKLOG B76 for what asking for it would cost.
    source_id: SourceId | None = None
    properties: dict[str, Any] = {}
    confidence: float

    @field_validator("relationship_type", "source_id", "properties")
    @classmethod
    def _reject_unstorable_in_free_form_text(cls, value: Passthrough) -> Passthrough:
        """No field reaching the event log may carry text that cannot be
        stored; see `domain/json_safety.py`. Listed per field for the reason
        `Entity` gives: the ids and the confidence cannot hold any."""
        reject_unstorable_text(value, what="relationship field")
        return value

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
