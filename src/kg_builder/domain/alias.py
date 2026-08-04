"""The `Alias` domain type: a record of one entity merged into another."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from kg_builder.domain.ids import EntityId, TenantId


class Alias(BaseModel):
    """One entity having been merged into another.

    **Not** a record of what the merge displaced. Undo is a compensating
    `MergeUndone` event carrying typed `RelationshipRedirection`s, so the log
    already holds the pre-merge state; a `displaced` dict here would be a
    second, unversioned copy of it. `extra="forbid"` is what makes removing
    that field visible: pydantic's default is to ignore an unknown keyword, so
    a call site still passing `displaced=` would keep constructing an `Alias`
    that quietly lost the data.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: TenantId
    canonical_entity_id: EntityId
    alias_entity_id: EntityId
    alias_name: str
    alias_normalized_name: str
    merged_at: datetime
    merge_reason: str | None = None

    @field_validator("merged_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("merged_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _reject_self_merge(self) -> Alias:
        if self.canonical_entity_id == self.alias_entity_id:
            raise ValueError("canonical_entity_id and alias_entity_id must differ")
        return self
