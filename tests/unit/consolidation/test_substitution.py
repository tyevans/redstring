"""A caller's own blocking and their own adjudicator, holding no library types.

`Consolidator.resolve` and `ConsolidationService.resolve` have always said
"supply one to change the weights or the blocking". Until `CandidateSource`
and `MergeAdjudicator` existed the parameters were typed against
`CandidateFinder` and `Adjudicator`, so taking the offer meant subclassing a
class whose `__init__` demands a `GraphStore` (and optionally a `VectorStore`,
a `FeatureWeights` and a flag) or an `LlmProvider` -- collaborators a caller
substituting the behaviour precisely does not have.

**Neither class here has a base class and neither constructs a library type.**
That is the whole assertion: `SearchIndexCandidates` holds a list, and
`ReviewQueue` holds a dict of decisions a human made earlier. If these stop
being usable, the substitution seam has closed again, and a test that built
its substitute by subclassing the default could not tell -- it would still
pass with the protocols deleted and the concrete annotations restored.

`ScoredCandidate` and `AdjudicationVerdict` are constructed, because they are
the protocol's own vocabulary rather than its collaborators: they are what the
two methods return, and both are exported.

## What these tests do and do not prove

Say it plainly, because the module reads stronger than it is: **most of the
runtime assertions below would have passed before the protocols existed.**
Python does not enforce annotations, so passing a duck-typed object where
`CandidateFinder` was declared always *worked*; it was `mypy --strict` that
refused it, and a caller who ran the gate the way this repo does would have
been stopped. Measured both ways rather than assumed:

    x: CandidateFinder = SearchIndexCandidates([])   # error: incompatible types
    x: CandidateSource = SearchIndexCandidates([])   # Success

So the standing gate for the seam is the type checker, over the annotations in
`service.py` and `build_graph.py`. What these tests add on top is the part
mypy cannot state: that a substitute holding none of the defaults'
collaborators actually drives a merge, is actually consulted about the band,
and that its `None` is not read as a yes. The two `isinstance` assertions are
the exception -- they fail outright without the `runtime_checkable` protocols,
which is what keeps this module from silently becoming a test of nothing if
the protocols are deleted and the concrete annotations restored.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from eventsource.adapters.memory import InMemoryEventStore, InMemorySnapshotStore

from redstring.consolidation.candidates import ScoredCandidate, SimilarityFeatures
from redstring.consolidation.policy import AdjudicationVerdict
from redstring.consolidation.protocols import CandidateSource, MergeAdjudicator
from redstring.consolidation.service import ConsolidationService
from redstring.graph.adapters.memory import InMemoryGraphStore

from .conftest import entity

if TYPE_CHECKING:
    from redstring.domain.entity import Entity


class SearchIndexCandidates:
    """What a caller with their own search index writes. No library base class."""

    def __init__(self, found: list[tuple[Entity, float]]) -> None:
        self._found = found

    async def candidates(
        self, subject: Entity, *, minimum_score: float = 0.0
    ) -> list[ScoredCandidate]:
        scored = [
            ScoredCandidate(
                found,
                SimilarityFeatures(name=score, embedding=None, graph=None),
                score,
            )
            for found, score in self._found
            if score >= minimum_score
        ]
        # The protocol requires a total order, not merely a sort by score.
        return sorted(scored, key=lambda c: (-c.score, str(c.entity.id)))


class ReviewQueue:
    """A human queue standing in for the model. Holds no `LlmProvider`."""

    def __init__(self, decisions: dict[str, bool]) -> None:
        self._decisions = decisions
        self.asked: list[str] = []

    async def adjudicate(self, subject, candidates) -> list[AdjudicationVerdict | None]:
        verdicts: list[AdjudicationVerdict | None] = []
        for candidate in candidates:
            name = candidate.entity.name
            self.asked.append(name)
            if name not in self._decisions:
                # No answer is `None`, never a fabricated "not the same".
                verdicts.append(None)
            else:
                verdicts.append(
                    AdjudicationVerdict(
                        same=self._decisions[name],
                        reason="reviewed by a person",
                        confidence=1.0,
                    )
                )
        return verdicts

    async def adjudicate_many(self, work) -> list[list[AdjudicationVerdict | None]]:
        """Delegates to `adjudicate` per subject.

        A queue with no notion of cross-subject batches is still a
        `MergeAdjudicator` -- `resolve_many` needs this method on the
        protocol because it is the only one it calls, but nothing requires
        an implementation to batch across subjects to provide it.
        """
        return [await self.adjudicate(subject, candidates) for subject, candidates in work]


def service(graph_store: InMemoryGraphStore) -> ConsolidationService:
    return ConsolidationService(
        event_store=InMemoryEventStore(),
        snapshot_store=InMemorySnapshotStore(),
        graph_store=graph_store,
    )


class TestTheProtocolsAcceptAForeignImplementation:
    def test_neither_substitute_is_an_instance_of_the_default(self) -> None:
        # If these were subclasses, every assertion below would hold with the
        # protocols reverted -- so this is what makes the rest of the module
        # evidence about the seam rather than about the defaults.
        from redstring.consolidation.candidates import CandidateFinder
        from redstring.consolidation.policy import Adjudicator

        assert not isinstance(SearchIndexCandidates([]), CandidateFinder)
        assert not isinstance(ReviewQueue({}), Adjudicator)

        assert isinstance(SearchIndexCandidates([]), CandidateSource)
        assert isinstance(ReviewQueue({}), MergeAdjudicator)

    async def test_a_foreign_candidate_source_drives_a_merge(self) -> None:
        tenant_id = uuid4()
        store = InMemoryGraphStore()
        subject = entity(tenant_id, name="Ada Lovelace")
        duplicate = entity(tenant_id, name="A. Lovelace")
        await store.upsert_entities([subject, duplicate])

        event = await service(store).resolve(
            subject,
            # Scored above `high` by the caller's own index, on a pair whose
            # string similarity would not have reached it.
            finder=SearchIndexCandidates([(duplicate, 0.99)]),
        )

        assert event is not None
        assert list(event.merged_entity_ids) == [duplicate.id]

    async def test_a_foreign_adjudicator_settles_the_band(self) -> None:
        tenant_id = uuid4()
        store = InMemoryGraphStore()
        subject = entity(tenant_id, name="Ada Lovelace")
        yes = entity(tenant_id, name="Ada Lovegood")
        no = entity(tenant_id, name="Ada Loveridge")
        await store.upsert_entities([subject, yes, no])

        queue = ReviewQueue({"Ada Lovegood": True, "Ada Loveridge": False})
        event = await service(store).resolve(
            subject,
            finder=SearchIndexCandidates([(yes, 0.87), (no, 0.86)]),
            adjudicator=queue,
        )

        assert set(queue.asked) == {"Ada Lovegood", "Ada Loveridge"}
        assert event is not None
        # The rejected one is not merged, which is what distinguishes a
        # consulted adjudicator from an ignored one.
        assert list(event.merged_entity_ids) == [yes.id]

    async def test_an_adjudicator_with_no_answer_does_not_merge(self) -> None:
        # `None` must not be read as a yes. A review queue nobody has worked
        # through yet is the ordinary way to produce one.
        tenant_id = uuid4()
        store = InMemoryGraphStore()
        subject = entity(tenant_id, name="Ada Lovelace")
        pending = entity(tenant_id, name="Ada Lovegood")
        await store.upsert_entities([subject, pending])

        event = await service(store).resolve(
            subject,
            finder=SearchIndexCandidates([(pending, 0.87)]),
            adjudicator=ReviewQueue({}),
        )

        assert event is None

    @pytest.mark.parametrize("minimum", [0.0, 0.5, 0.9])
    def test_the_source_is_asked_to_apply_the_low_threshold(self, minimum: float) -> None:
        # `resolve` passes `low` as `minimum_score` rather than filtering
        # afterwards, so a substitute that ignores the argument changes which
        # pairs reach the band. Pinned here because the service's own test
        # cannot see it -- `CandidateFinder` applies it correctly.
        tenant_id = uuid4()
        low = entity(tenant_id, name="Zebedee Quill")
        source = SearchIndexCandidates([(low, 0.6)])

        import asyncio

        kept = asyncio.run(source.candidates(low, minimum_score=minimum))

        assert bool(kept) is (minimum <= 0.6)
