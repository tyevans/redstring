"""What happened to a tenant's entity graph: the `ConsolidationLog`'s events.

## Undo is a compensating event, and it carries what it restores

`MergeUndone` names the merge it reverses **and** carries the relationships to
put back. Naming alone would be enough for a reader of the whole log, but a
projection handler sees one event at a time: resolving `merge_event_id` would
mean a read of the log from inside a fold, which is what a projection exists
to avoid.

That does not make the payload the source of truth. The `ConsolidationLog`
aggregate rehydrates its merge history by replay, so when it is asked to undo
a merge it *derives* the restoration from replayed state and writes it into
the event. Recovery is by replay; the payload is that recovery, materialised
once at the boundary so every downstream consumer gets it for free.

The same reasoning covers entity data. A merge's whole effect on it is one
before/after pair on the canonical entity -- `EntitiesMerged.resolution` --
recorded rather than recomputed for the same reason `redirections` is: the
projection overwrote the pre-merge value when it applied the event, so by the
time anyone might want to recompute it, the input is gone.
"""

from __future__ import annotations

from collections import Counter
from uuid import UUID

from eventsource import register_event
from eventsource.domain.tenant_events import TenantDomainEvent
from pydantic import Field, model_validator

from redstring.domain.consolidation import (
    MergeableFields,
    PropertyResolution,
    RelationshipRedirection,
)
from redstring.domain.ids import EntityId
from redstring.domain.relationship import Relationship
from redstring.events.streams import CONSOLIDATION_CATEGORY


@register_event
class EntitiesMerged(TenantDomainEvent):
    """One or more entities absorbed into a canonical entity.

    `redirections` is the whole effect on the edge set: every edge that moved
    onto the canonical entity, and every edge the merge dropped because both
    its endpoints were absorbed. It is recorded here rather than recomputed by
    the projection because recomputing it needs the pre-merge graph, which by
    definition no longer exists once the projection has applied the event.

    `resolution` is the same idea applied to entity data rather than edges: a
    merge's whole effect on the canonical entity's mergeable fields is one
    before/after pair, recorded rather than recomputed for the reason above --
    the projection overwrote the pre-merge value when it applied the event.
    """

    event_version: int = 2
    aggregate_type: str = CONSOLIDATION_CATEGORY

    canonical_entity_id: EntityId
    merged_entity_ids: list[EntityId] = Field(min_length=1)
    merge_reason: str | None = None
    redirections: list[RelationshipRedirection] = Field(default_factory=list)
    resolution: PropertyResolution | None = None

    @model_validator(mode="after")
    def _the_merge_is_coherent(self) -> EntitiesMerged:
        if self.canonical_entity_id in self.merged_entity_ids:
            raise ValueError(f"an entity cannot be merged into itself: {self.canonical_entity_id}")
        # Named rather than counted. `len(set(x)) != len(x)` reads fine and is
        # what was here first, but a cosmic-ray mutant rewriting `!=` as
        # `is not` survived it: CPython interns small ints, so the two
        # spellings agree for every list short enough to appear in a test.
        # That is the same interning trap this project has hit before, and
        # collecting the offending ids sidesteps it -- as well as saying
        # *which* id repeated, which a length comparison never could.
        duplicates = sorted(
            str(entity_id)
            for entity_id, count in Counter(self.merged_entity_ids).items()
            if count > 1
        )
        if duplicates:
            raise ValueError(f"merged_entity_ids contains duplicates: {duplicates}")
        foreign = {
            r.before.tenant_id for r in self.redirections if r.before.tenant_id != self.tenant_id
        }
        if foreign:
            raise ValueError(
                f"redirections carry tenants the event does not belong to: "
                f"{sorted(str(t) for t in foreign)} != {self.tenant_id}"
            )
        if self.resolution is not None and self.resolution.entity_id != self.canonical_entity_id:
            raise ValueError(
                f"resolution must name the canonical entity: "
                f"{self.resolution.entity_id} != {self.canonical_entity_id}"
            )
        return self


@register_event
class MergeUndone(TenantDomainEvent):
    """A merge reversed. See the module docstring on why it carries a payload."""

    event_version: int = 1
    aggregate_type: str = CONSOLIDATION_CATEGORY

    merge_event_id: UUID
    canonical_entity_id: EntityId
    unmerged_entity_ids: list[EntityId] = Field(min_length=1)
    restored_relationships: list[Relationship] = Field(default_factory=list)
    restored_fields: MergeableFields | None = None

    @model_validator(mode="after")
    def _restorations_belong_to_this_tenant(self) -> MergeUndone:
        foreign = {
            r.tenant_id for r in self.restored_relationships if r.tenant_id != self.tenant_id
        }
        if foreign:
            raise ValueError(
                f"restored_relationships carry tenants the event does not belong "
                f"to: {sorted(str(t) for t in foreign)} != {self.tenant_id}"
            )
        return self
