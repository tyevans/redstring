"""Merging and undoing, as events on the `ConsolidationLog`.

**This service does not write to `GraphStore` or `VectorStore`.** It reads the
graph to work out what a merge would do, records that as an `EntitiesMerged`,
and stops. The projection applies it. A store write here would rebuild exactly
the thing the re-architecture removed: two sources of truth, one of them
undiscoverable from the log.

So the shape of every method is the same -- read, plan, emit -- and the only
thing that ever changes a read model is a projection folding an event.

## Why the read happens before the aggregate is loaded

`plan_redirections` needs the pre-merge edges, and the aggregate needs the plan
to put in the event. Reading first means the store read is not inside the
window where the aggregate holds its expected version, which keeps that window
as short as the write itself.

The cost is that the graph could change between the read and the append. That
is survivable and the alternative is not: the read model is a projection of the
log and lags it by construction, so there is no ordering of these two steps
that makes the graph authoritative. What protects correctness is the
aggregate's own state -- `MergeIntoAliasError` and `DoubleMergeError` are
checked against the replayed log, not against the graph -- and optimistic
concurrency on the tenant's stream, which is why the stream is the tenant.

A stale read can therefore produce a redirection for an edge that no longer
exists, or miss one that has just appeared. The first is harmless -- both
writes are idempotent. The second has two outcomes, and only one of them
heals: the extraction fold resolves the endpoint on the next
`DocumentExtracted`, but if the canonical entity already carries the same
claim, that resolution *creates a permanent parallel edge* rather than fixing
one. BACKLOG **B43**, pinned in
`tests/unit/consolidation/test_known_gaps.py`.

## `resolve` is the whole pipeline, and it is what closes B40

Slice 6 deleted `SimpleMerger`/`LLMMerger` rather than porting them, because
they resolved entities inside extraction with no event to audit or undo.
`resolve` is that policy rebuilt where a judgement becomes an
`EntitiesMerged` -- blocking, scoring, the two thresholds, and one batched
model call for the band between them.

## Undo derives its payload from the log, not from the caller

`undo_merge` takes an event id and nothing else. The aggregate rehydrates its
merge history and writes the restoration into `MergeUndone` itself. A caller
supplying what to restore would be a caller able to restore something that
never happened.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from eventsource.domain.tenant_context import tenant_scope

from redstring.aggregates.repositories import consolidation_repository
from redstring.consolidation.planning import plan_redirections
from redstring.consolidation.policy import (
    HIGH_SIMILARITY,
    LOW_SIMILARITY,
    MergeDecision,
    decide,
)
from redstring.events.streams import consolidation_stream

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from eventsource.ports.snapshots import SnapshotStore
    from eventsource.ports.store import AggregateStore

    from redstring.consolidation.candidates import ScoredCandidate
    from redstring.consolidation.protocols import CandidateSource, MergeAdjudicator
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, TenantId
    from redstring.events.merge import EntitiesMerged, MergeUndone
    from redstring.ports.graph_store import RelationshipStore


class ConsolidationService:
    """Emits merges and undos. Reads the graph; never writes to it."""

    def __init__(
        self,
        *,
        event_store: AggregateStore,
        snapshot_store: SnapshotStore,
        graph_store: RelationshipStore,
    ) -> None:
        self._graph = graph_store
        self._log = consolidation_repository(event_store, snapshot_store)

    async def merge(
        self,
        *,
        tenant_id: TenantId,
        canonical_entity_id: EntityId,
        merged_entity_ids: Sequence[EntityId],
        merge_reason: str | None = None,
    ) -> EntitiesMerged:
        """Absorb `merged_entity_ids` into `canonical_entity_id`.

        Returns the emitted event, whose `event_id` is what `undo` takes. It is
        returned rather than looked up afterwards because the caller has no
        other way to name the merge it just made -- and an undo naming the
        wrong merge is the one mistake `UnknownMergeError` cannot catch, since
        both would be merges that happened.

        Raises `MergeIntoAliasError` or `DoubleMergeError` before writing
        anything. Both are refusals to record a fact rather than failures to
        write one, so there is nothing half-applied to clean up.
        """
        group = [canonical_entity_id, *merged_entity_ids]
        relationships = await self._graph.get_relationships_for(group, tenant_id)
        redirections = plan_redirections(
            canonical_entity_id=canonical_entity_id,
            merged_entity_ids=merged_entity_ids,
            relationships=relationships,
        )

        async with tenant_scope(tenant_id):
            log = await self._log.load_or_create(
                consolidation_stream(tenant_id=tenant_id).aggregate_id
            )
            event = log.merge(
                tenant_id=tenant_id,
                canonical_entity_id=canonical_entity_id,
                merged_entity_ids=merged_entity_ids,
                merge_reason=merge_reason,
                redirections=redirections,
            )
            await self._log.save(log)
        return event

    async def resolve(
        self,
        subject: Entity,
        *,
        finder: CandidateSource,
        adjudicator: MergeAdjudicator | None = None,
        high: float = HIGH_SIMILARITY,
        low: float = LOW_SIMILARITY,
    ) -> EntitiesMerged | None:
        """Find `subject`'s duplicates, decide, and merge them if any.

        The whole B40 pipeline in one call: block, score, band, ask a model
        about the band, emit **one** `EntitiesMerged` for everything that came
        out `MERGE`.

        Returns the event, or `None` when nothing was decided worth merging --
        which is the common case and is not a failure.

        Args:
            subject: The entity to consolidate around. It becomes the canonical
                entity, so a caller choosing which of two duplicates to pass is
                choosing which survives.

                An entity that has itself been merged away raises
                `MergeIntoAliasError` rather than returning `None`, and that
                asymmetry with the *candidates* -- which are silently excluded
                -- is deliberate. An aliased candidate is one of many and
                dropping it costs nothing; an aliased subject means the caller
                asked to consolidate around an entity that no longer stands
                for itself, and answering `None` would be indistinguishable
                from "no duplicates found". A caller sweeping a whole tenant
                should resolve its ids first: `find_entities` returns absorbed
                entities too, because a merge is not a delete.
            finder: Supplies and scores the candidates.
            adjudicator: Consulted for the band between `low` and `high`.
                Without one the band is **rejected**, not merged: the whole
                point of the band is that the score alone does not settle it,
                so treating "nobody asked" as a yes would merge exactly the
                pairs a model was there to protect.
            high: At or above, merge without asking.
            low: Below, never merge and never ask.

        One event for the whole group, not one per pair, and that is not
        merely tidier. `ConsolidationLog` refuses to merge an entity twice, so
        two separate merges into one canonical entity would be legal but would
        produce two `RelationshipRedirection` sets computed against different
        graphs -- the second one against a graph the first had already changed.

        A candidate the model rejects is dropped silently from this call, and
        nothing records that it was considered. That is BACKLOG **B44**: the
        rejections are the training data for tuning the thresholds, and they
        are being thrown away.
        """
        # `minimum_score=low` means `decide` below can never answer `REJECT`:
        # the finder has already dropped everything under the low threshold.
        # Worth knowing, because it is why two cosmic-ray mutants rewriting the
        # band comparisons as `>=` and `<=` survived -- they differ from `is`
        # only on `REJECT`, which is not in this list. The filter is here
        # rather than after `decide` so the rejected pairs are never scored
        # into a list only to be dropped from it.
        candidates = await finder.candidates(subject, minimum_score=low)
        if not candidates:
            return None

        banded = [
            (candidate, decide(candidate.score, high=high, low=low)) for candidate in candidates
        ]
        undecided = [c for c, decision in banded if decision is MergeDecision.ADJUDICATE]
        # A candidate and the reason it is being merged, carried together
        # rather than in two lists kept aligned by hand -- the reason is what
        # lands on `merge_reason`, and a merge attributed to the wrong reason
        # is an audit trail that lies while looking complete.
        confirmed: list[tuple[ScoredCandidate, str]] = [
            (c, f"score >= {high}") for c, decision in banded if decision is MergeDecision.MERGE
        ]

        if undecided and adjudicator is not None:
            verdicts = await adjudicator.adjudicate(subject, undecided)
            confirmed += [
                (candidate, verdict.reason)
                # `verdict is None` is "the model did not answer", which is not
                # a yes. See `policy.Adjudicator.adjudicate`.
                for candidate, verdict in zip(undecided, verdicts, strict=True)
                if verdict is not None and verdict.same
            ]

        if not confirmed:
            return None
        return await self.merge(
            tenant_id=subject.tenant_id,
            canonical_entity_id=subject.id,
            merged_entity_ids=[candidate.entity.id for candidate, _ in confirmed],
            merge_reason="; ".join(reason for _, reason in confirmed),
        )

    async def undo(self, *, tenant_id: TenantId, merge_event_id: UUID) -> MergeUndone:
        """Reverse the merge `merge_event_id` recorded.

        Raises `UnknownMergeError` when no merge with that id is in effect --
        which covers both "never happened" and "already undone", deliberately:
        from here they are one case, and there is nothing to reverse either
        way.

        No graph read at all. Everything the undo restores is in the log.

        **This overwrites concurrent edits, and that is the intended reading.**
        The projection upserts every `before` relationship, so an edge
        legitimately modified between the merge and the undo is restored to
        its pre-merge value rather than merged with the newer one. A
        compensating event's job is to reproduce the state before the event it
        compensates -- the round-trip test asserts exactly that -- and an undo
        that preserved intervening edits would reproduce something else. Said
        out loud because it is a real choice that looks like an oversight.
        """
        async with tenant_scope(tenant_id):
            log = await self._log.load_or_create(
                consolidation_stream(tenant_id=tenant_id).aggregate_id
            )
            event = log.undo_merge(tenant_id=tenant_id, merge_event_id=merge_event_id)
            await self._log.save(log)
        return event
