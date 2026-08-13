"""One tenant's merge history, and the three rules it enforces.

The stream is the tenant, deliberately: two concurrent merges touching the
same entities must not interleave, and a per-tenant stream with optimistic
concurrency is what stops them. That serialises a tenant's merges, which is
the cost, and it is the right cost -- consolidation genuinely spans documents,
so there is no narrower boundary that still sees the conflicts.

The stream is therefore long, and grows with a tenant's merge history rather
than with anything bounded. `EveryNEvents` snapshots keep rehydration bounded;
see `redstring.aggregates.repositories`.

## Why these rules live here and not in a service

Each of the three corrupts a graph *silently*:

- **No merging into an alias.** B into A, then C into B, leaves C pointing at
  something that is not canonical. Nothing in `GraphStore` resolves a chain,
  so C's edges simply end up on the wrong entity.
- **No merging an entity twice.** B absorbed by A and then by C gives B two
  canonical parents, and which one wins depends on the order the projection
  happened to fold them in.
- **An undo must reference a merge in effect.** Otherwise it restores edges
  that were never displaced -- writing a pre-merge graph over a graph that was
  never merged.

A service can check all three. What it cannot do is check them against a
consistent view of the tenant's history while holding the write lock that
makes the check meaningful, which is exactly what an aggregate plus
`ExpectedVersion` gives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from eventsource.domain.aggregate import AggregateRoot
from pydantic import BaseModel, Field

from redstring.domain.consolidation import PropertyResolution, RelationshipRedirection
from redstring.domain.exceptions import (
    DoubleMergeError,
    MergeIntoAliasError,
    UnknownMergeError,
)
from redstring.domain.ids import EntityId
from redstring.events.merge import EntitiesMerged, MergeUndone
from redstring.events.streams import CONSOLIDATION_CATEGORY

if TYPE_CHECKING:
    from collections.abc import Sequence

    from eventsource.domain.event import DomainEvent

    from redstring.domain.ids import TenantId


class MergeRecord(BaseModel):
    """One merge as the log remembers it.

    Keeps the `redirections` because that is what an undo restores. Nothing
    else does: reconstructing them at undo time would need the pre-merge
    graph, which the projection overwrote when it applied the merge.

    `resolution` is kept for the same reason `redirections` is: an undo
    restores it, and reconstructing it later would need the pre-merge entity
    the projection overwrote.
    """

    merge_event_id: UUID
    canonical_entity_id: EntityId
    merged_entity_ids: list[EntityId]
    redirections: list[RelationshipRedirection] = Field(default_factory=list)
    resolution: PropertyResolution | None = None
    undone: bool = False


class ConsolidationLogState(BaseModel):
    """State the invariants are checked against.

    `alias_of` is derived from `merges` and could be recomputed on every
    command. It is materialised because the "is this an alias" check runs
    once per entity per merge, and a tenant's merge list is unbounded --
    recomputing would make each merge cost the whole history.

    Both fields are `list`/`dict` rather than `set`, because state is
    snapshotted through `model_dump(mode="json")` and a `set` has no JSON
    form. Order in `merged_entity_ids` is not meaningful; it is preserved
    only because a list preserves it.
    """

    merges: list[MergeRecord] = Field(default_factory=list)
    alias_of: dict[EntityId, EntityId] = Field(default_factory=dict)


class ConsolidationLog(AggregateRoot[ConsolidationLogState]):
    """The write model for one tenant's merges. Aggregate id **is** the tenant."""

    aggregate_type = CONSOLIDATION_CATEGORY

    def _get_initial_state(self) -> ConsolidationLogState:
        return ConsolidationLogState()

    @property
    def _current(self) -> ConsolidationLogState:
        if self._state is None:
            self._state = self._get_initial_state()
        return self._state

    def merge(
        self,
        *,
        tenant_id: TenantId,
        canonical_entity_id: EntityId,
        merged_entity_ids: Sequence[EntityId],
        merge_reason: str | None = None,
        redirections: Sequence[RelationshipRedirection] = (),
        resolution: PropertyResolution | None = None,
    ) -> EntitiesMerged:
        """Absorb `merged_entity_ids` into `canonical_entity_id`.

        Raises `MergeIntoAliasError` or `DoubleMergeError` rather than
        emitting. Both are refusals to record a fact, not failures to write
        one, so there is no half-applied state to clean up.
        """
        state = self._current

        canonical_of = state.alias_of.get(canonical_entity_id)
        if canonical_of is not None:
            raise MergeIntoAliasError(
                alias_entity_id=canonical_entity_id, canonical_entity_id=canonical_of
            )

        # Every element, not just the first: a merge absorbs a batch, and a
        # check that stopped early would let anything behind a legal id
        # through.
        for entity_id in merged_entity_ids:
            existing = state.alias_of.get(entity_id)
            if existing is not None:
                raise DoubleMergeError(entity_id=entity_id, canonical_entity_id=existing)

        return self.create_event(
            EntitiesMerged,
            tenant_id=tenant_id,
            canonical_entity_id=canonical_entity_id,
            merged_entity_ids=list(merged_entity_ids),
            merge_reason=merge_reason,
            redirections=list(redirections),
            resolution=resolution,
        )

    def undo_merge(self, *, tenant_id: TenantId, merge_event_id: UUID) -> MergeUndone:
        """Reverse the merge `merge_event_id` recorded.

        The restoration is **derived from replayed state**, not supplied by
        the caller: the aggregate rebuilt its merge history from the log, so
        it knows what the merge displaced. Writing that into the event is what
        lets a projection handler apply the undo without reading the log.
        """
        record = self._merge_in_effect(merge_event_id)
        return self.create_event(
            MergeUndone,
            tenant_id=tenant_id,
            merge_event_id=merge_event_id,
            canonical_entity_id=record.canonical_entity_id,
            unmerged_entity_ids=list(record.merged_entity_ids),
            restored_relationships=[r.before for r in record.redirections],
            restored_fields=record.resolution.before if record.resolution is not None else None,
        )

    def _merge_in_effect(self, merge_event_id: UUID) -> MergeRecord:
        for record in self._current.merges:
            if record.merge_event_id == merge_event_id and not record.undone:
                return record
        raise UnknownMergeError(merge_event_id=merge_event_id)

    def _apply(self, event: DomainEvent) -> None:
        if isinstance(event, EntitiesMerged):
            self._apply_merged(event)
        elif isinstance(event, MergeUndone):
            self._apply_undone(event)

    def _apply_merged(self, event: EntitiesMerged) -> None:
        state = self._current
        state.merges.append(
            MergeRecord(
                merge_event_id=event.event_id,
                canonical_entity_id=event.canonical_entity_id,
                merged_entity_ids=list(event.merged_entity_ids),
                redirections=list(event.redirections),
                resolution=event.resolution,
            )
        )
        for entity_id in event.merged_entity_ids:
            state.alias_of[entity_id] = event.canonical_entity_id

    def _apply_undone(self, event: MergeUndone) -> None:
        state = self._current
        for record in state.merges:
            if record.merge_event_id == event.merge_event_id:
                record.undone = True
        # The entities stop being aliases, which is what makes correcting a bad
        # merge possible rather than merely recorded.
        #
        # Unconditional, after a guard here turned out to be unreachable. It
        # read `if state.alias_of.get(entity_id) == event.canonical_entity_id`
        # -- "clear this only if a later merge has not claimed the entity" --
        # but a later merge cannot have. `merge` refuses any entity already in
        # `alias_of`, so between a merge and its undo the entity has exactly
        # one canonical parent, and replay applies each event once, in order.
        # No valid history can falsify the condition, and a branch no valid
        # history reaches is worse than no branch: it describes a situation
        # that cannot arise, so a reader reasons about the wrong invariant.
        # Found by a cosmic-ray mutant that rewrote the `==` as `<=` and
        # survived, which is what an unreachable guard looks like from outside.
        #
        # `pop` with a default rather than `del`: a redelivered `MergeUndone`
        # finds the entry already gone, and that is idempotent, not an error.
        for entity_id in event.unmerged_entity_ids:
            state.alias_of.pop(entity_id, None)
