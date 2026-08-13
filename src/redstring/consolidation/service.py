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
from redstring.consolidation.planning import plan_properties, plan_redirections
from redstring.consolidation.policy import (
    HIGH_SIMILARITY,
    LOW_SIMILARITY,
    MergeDecision,
    decide,
)
from redstring.domain.exceptions import MergeIntoAliasError, MissingEntityError
from redstring.domain.merge_strategy import PropertyMergePolicy
from redstring.events.streams import consolidation_stream

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from eventsource.ports.snapshots import SnapshotStore
    from eventsource.ports.store import AggregateStore

    from redstring.consolidation.candidates import ScoredCandidate
    from redstring.consolidation.protocols import (
        CandidateSource,
        ConsolidationGraph,
        MergeAdjudicator,
    )
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId, TenantId
    from redstring.events.merge import EntitiesMerged, MergeUndone


class ConsolidationService:
    """Emits merges and undos. Reads the graph; never writes to it.

    `merge` now reads entities as well as edges: absorbing a duplicate
    decides not only what happens to its edges (`plan_redirections`) but what
    happens to the canonical entity's own `description`, `properties` and
    `external_ids` (`plan_properties`). The policy that decision follows
    lives here, constructed once at wiring time, because most callers want
    one merge policy for the whole service and only occasionally need to
    override it for a single call -- `merge`'s own `policy` argument takes
    precedence when supplied.
    """

    def __init__(
        self,
        *,
        event_store: AggregateStore,
        snapshot_store: SnapshotStore,
        graph_store: ConsolidationGraph,
        merge_policy: PropertyMergePolicy | None = None,
    ) -> None:
        """`graph_store` is the wider `ConsolidationGraph` slice, not just `RelationshipStore`.

        `resolve` needs to read entities and resolve aliases before it plans
        redirections, not only edges -- see `resolve`'s handling of a subject
        that has itself been merged away.

        `merge_policy` is the default `PropertyMergePolicy` `merge` uses to
        decide the canonical entity's fields when a call does not supply its
        own. `PropertyMergePolicy()` -- prefer the canonical entity's values
        -- when omitted.
        """
        self._graph = graph_store
        self._log = consolidation_repository(event_store, snapshot_store)
        self._policy = merge_policy if merge_policy is not None else PropertyMergePolicy()

    async def merge(
        self,
        *,
        tenant_id: TenantId,
        canonical_entity_id: EntityId,
        merged_entity_ids: Sequence[EntityId],
        merge_reason: str | None = None,
        policy: PropertyMergePolicy | None = None,
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

        Args:
            policy: Overrides this service's `merge_policy` for this call
                only. Omit to use the service's own policy.
        """
        group = [canonical_entity_id, *merged_entity_ids]
        relationships = await self._graph.get_relationships_for(group, tenant_id)
        redirections = plan_redirections(
            canonical_entity_id=canonical_entity_id,
            merged_entity_ids=merged_entity_ids,
            relationships=relationships,
        )

        entities = await self._graph.get_entities(group, tenant_id)
        by_id = {entity.id: entity for entity in entities}
        canonical = by_id.get(canonical_entity_id)
        if canonical is None:
            # The log and the graph disagreeing, not a routine miss: a merge
            # whose canonical entity has no row cannot decide anything about
            # its fields, and guessing would write a decision nobody made.
            # Same reading as `_resolved_subject`.
            raise MissingEntityError(entity_id=canonical_entity_id, tenant_id=tenant_id)
        # An absorbed entity with no row is *tolerated*, deliberately:
        # `GraphProjection._apply_merge` already writes an alias with a null
        # name for exactly this case, and refusing here would make the plan
        # stricter than the fold that applies it.
        others = [by_id[entity_id] for entity_id in merged_entity_ids if entity_id in by_id]
        resolution = plan_properties(
            policy=policy if policy is not None else self._policy,
            canonical=canonical,
            others=others,
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
                resolution=resolution,
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

                **Resolved to its terminal canonical first, if it has itself
                been merged away.** If A was merged into B, consolidating
                around A means consolidating around B -- a merge is exactly
                the assertion that they are one entity, so a new duplicate C
                belongs with B whichever of A or B the caller happened to
                pass. Resolution is transitive
                (`GraphStore.resolve_entity_ids` follows a chain to its end,
                not one hop -- `B -> A` then `A -> C` resolves to `C`) and
                terminates by construction: `ConsolidationLog.merge` refuses
                to merge *into* an alias, so no chain can point back on
                itself, and adapters bound the walk anyway and raise
                `AliasCycleError` on data that violates that invariant rather
                than trust it and risk a hang on a corrupt row. This does not
                touch anything about the entity that resolved through -- its
                relationships and properties were already folded into the
                canonical by the merge that made it an alias, and this call
                neither re-derives nor re-applies that; it only decides which
                entity *new* duplicates, if any, get merged into. Resolved
                before `finder` is even asked for candidates, not by catching
                `MergeIntoAliasError` after the fact, so a resolved subject is
                scored with the neighbours it actually has -- an alias's own
                edges have already been redirected away, so scoring against
                it directly would starve the graph feature for no reason.
                `MergeIntoAliasError` can still surface, but only from a
                genuine race (the resolved canonical becoming an alias
                itself between this resolution and the merge below); that
                case is retried once against the newer canonical rather than
                surfaced, since the caller did nothing wrong.
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
        subject = await self._resolved_subject(subject)

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

        confirmed_ids = [candidate.entity.id for candidate, _ in confirmed]
        merge_reason = "; ".join(reason for _, reason in confirmed)
        try:
            return await self.merge(
                tenant_id=subject.tenant_id,
                canonical_entity_id=subject.id,
                merged_entity_ids=confirmed_ids,
                merge_reason=merge_reason,
            )
        except MergeIntoAliasError:
            # `subject` was already resolved to its canonical above, before
            # `finder` ran, so this is not the stale-subject case -- it is a
            # genuine race: something merged this call's canonical into
            # something else in the window between that resolution and the
            # append inside `merge`. Retried once against the new canonical,
            # because the caller did nothing wrong the first time either.
            # Not retried a second time: two races on one call is not the
            # same event happening twice, it is a sign something is
            # genuinely wrong, and that should surface rather than loop.
            subject = await self._resolved_subject(subject)
            return await self.merge(
                tenant_id=subject.tenant_id,
                canonical_entity_id=subject.id,
                merged_entity_ids=confirmed_ids,
                merge_reason=merge_reason,
            )

    async def _resolved_subject(self, subject: Entity) -> Entity:
        """`subject`, or the entity now standing for it if it was itself merged away.

        See `resolve`'s docstring for why following the alias chain here is
        transitive, terminates by construction, and is safe to call more than
        once for the same subject (idempotent once resolution reaches a
        canonical -- `resolve_entity_ids` maps a canonical id to itself).
        """
        resolved = await self._graph.resolve_entity_ids([subject.id], subject.tenant_id)
        canonical_id = resolved[subject.id]
        if canonical_id == subject.id:
            return subject
        canonical = await self._graph.get_entity(canonical_id, subject.tenant_id)
        if canonical is None:
            # `resolve_entity_ids` named a canonical the store has no row
            # for. A merge is not a delete, so the canonical of a live alias
            # should always be readable -- this is the log and the graph
            # disagreeing, not a routine miss, and `MissingEntityError`
            # already names exactly that shape of inconsistency.
            raise MissingEntityError(entity_id=canonical_id, tenant_id=subject.tenant_id)
        return canonical

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

        The same is true of the canonical entity's fields. `GraphProjection`
        restores `resolution.before` unconditionally over whatever
        `description`, `external_ids` and `properties` the canonical entity
        holds at undo time -- so a `DocumentExtracted` that changed one of
        those fields between the merge and the undo is silently reverted
        along with the merge, for the identical reason relationships are:
        undo reproduces the pre-merge state, not the latest one.
        """
        async with tenant_scope(tenant_id):
            log = await self._log.load_or_create(
                consolidation_stream(tenant_id=tenant_id).aggregate_id
            )
            event = log.undo_merge(tenant_id=tenant_id, merge_event_id=merge_event_id)
            await self._log.save(log)
        return event
