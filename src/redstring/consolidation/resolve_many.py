"""Multi-subject consolidation pass implementation across wavefronts."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from redstring.consolidation.policy import HIGH_SIMILARITY, LOW_SIMILARITY
from redstring.domain.limiter import CallLimiter

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from redstring.consolidation.banded import BandedCandidates
    from redstring.consolidation.candidates import ScoredCandidate
    from redstring.consolidation.protocols import (
        CandidateSource,
        MergeAdjudicator,
    )
    from redstring.consolidation.service import ConsolidationService
    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId
    from redstring.events.merge import EntitiesMerged


def _batches[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    """Consecutive slices of at most `size`. The last may be short."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def _still_mergeable(
    service: ConsolidationService,
    decision: BandedCandidates,
    confirmed: list[tuple[ScoredCandidate, str]],
    in_pass_alias_of: dict[EntityId, EntityId],
) -> list[tuple[ScoredCandidate, str]] | None:
    """`confirmed` minus anything an earlier emit in this pass consumed.

    `None` when the subject itself has been merged away, which drops the
    whole decision -- see `resolve_many` for why that is a skip rather
    than a retry.

    Checks two things, because they catch different windows: the graph
    (staleness from before this pass began) and `in_pass_alias_of`
    (staleness from an emit earlier in this same pass, which the graph
    cannot see -- see `resolve_many`).
    """
    subject = decision.subject
    ids = [subject.id, *(candidate.entity.id for candidate, _ in confirmed)]
    canonical = await service._graph.resolve_entity_ids(ids, subject.tenant_id)

    def is_alias(entity_id: EntityId) -> bool:
        return canonical[entity_id] != entity_id or entity_id in in_pass_alias_of

    if is_alias(subject.id):
        return None
    return [
        (candidate, reason) for candidate, reason in confirmed if not is_alias(candidate.entity.id)
    ]


async def execute_resolve_many(
    service: ConsolidationService,
    subjects: Sequence[Entity],
    *,
    finder: CandidateSource,
    adjudicator: MergeAdjudicator | None = None,
    concurrency: int = 1,
    limiter: CallLimiter | None = None,
    high: float = HIGH_SIMILARITY,
    low: float = LOW_SIMILARITY,
) -> list[EntitiesMerged]:
    """Execute a multi-subject consolidation pass across wavefronts.

    Phases:
      1. Phase 1 (concurrent): score and band every subject in wavefronts.
      2. Phase 2 (barrier): adjudicate uncertain candidates in batches.
      3. Phase 3 (serial): emit merges serially in deterministic order,
         filtering out any aliases consumed during earlier iterations of this pass.
    """
    if concurrency < 1:
        raise ValueError(f"concurrency must be >= 1, got {concurrency}")
    if not subjects:
        return []
    limiter = limiter if limiter is not None else CallLimiter(concurrency)

    # Phase 1 -- score and band, in wavefronts of `concurrency`.
    banded: list[BandedCandidates] = []
    for batch in _batches(subjects, concurrency):
        results = await asyncio.gather(
            *(
                service._score_and_band(subject, finder=finder, high=high, low=low)
                for subject in batch
            )
        )
        banded.extend(result for result in results if result is not None)

    # Emit order is derived rather than inherited from `subjects`, so two
    # runs over one graph agree regardless of what order phase 1 finished
    # in. The subject id as a string, matching how `CandidateFinder`
    # breaks score ties -- one convention for "an arbitrary but total
    # order over entities", not two.
    banded.sort(key=lambda decision: str(decision.subject.id))

    # Phase 2 -- adjudicate, in batches spanning subjects.
    confirmed_per_subject = [list(decision.confirmed) for decision in banded]
    if adjudicator is not None:
        work = [(decision.subject, decision.undecided) for decision in banded if decision.undecided]
        if work:
            async with limiter:
                verdict_lists = await adjudicator.adjudicate_many(work)
            by_subject = {
                subject.id: verdicts
                for (subject, _), verdicts in zip(work, verdict_lists, strict=True)
            }
            for index, decision in enumerate(banded):
                verdicts = by_subject.get(decision.subject.id)
                if verdicts is None:
                    continue
                confirmed_per_subject[index] += [
                    (candidate, verdict.reason)
                    # `verdict is None` is "the model did not answer",
                    # which is not a yes.
                    for candidate, verdict in zip(decision.undecided, verdicts, strict=True)
                    if verdict is not None and verdict.same
                ]

    # Phase 3 -- emit, serially, re-resolving as we go.
    events: list[EntitiesMerged] = []
    in_pass_alias_of: dict[EntityId, EntityId] = {}
    for decision, confirmed in zip(banded, confirmed_per_subject, strict=True):
        if not confirmed:
            continue
        fresh = await _still_mergeable(service, decision, confirmed, in_pass_alias_of)
        if fresh is None:
            continue
        event = await service._emit(decision, fresh)
        if event is not None:
            events.append(event)
            for merged_id in event.merged_entity_ids:
                in_pass_alias_of[merged_id] = event.canonical_entity_id
    return events
