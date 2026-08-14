"""`ConsolidationService.resolve_many`: a corpus-level pass, decide then emit.

Reuses `Rig` and `keyed` from `test_resolve.py` -- the fixtures and store
setup for the single-subject pipeline are exactly what the corpus pass is
built on. Every entity id here is pinned (`UUID(int=...)`), never `uuid4()`:
the mutual-confirmation cases below depend on a deterministic emit order
(ascending subject id, as a string), and a random id would make "which
subject wins" a coin flip.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from redstring.domain.limiter import CallLimiter

from .test_resolve import Rig, keyed


def uid(n: int) -> UUID:
    return UUID(int=n)


#: Distinct enough that every pair scores well under `LOW_SIMILARITY` (0.75)
#: -- confirmed directly against `string_similarity`, not assumed from how
#: different the names look. Numbered names like "Group1 Person" /
#: "Group2 Person" score ~0.97 against each other (one digit apart in an
#: otherwise identical string) and merge by accident, which is what the first
#: draft of this file did.
DISTINCT_NAMES = ["Ada Lovelace", "Zebedee Quill", "Mercy Okafor", "Yusuf Demir", "Priya Nair"]


class RecordingFinder:
    """Wraps a real `CandidateSource`, timestamping when each call returns."""

    def __init__(self, real) -> None:
        self._real = real
        self.finished_at: list[float] = []

    async def candidates(self, subject, *, minimum_score=0.0):
        result = await self._real.candidates(subject, minimum_score=minimum_score)
        self.finished_at.append(asyncio.get_running_loop().time())
        return result


class TrackingFinder:
    """Records the high-water mark of concurrent `candidates` calls in flight."""

    def __init__(self, real) -> None:
        self._real = real
        self._current = 0
        self.max_in_flight = 0

    async def candidates(self, subject, *, minimum_score=0.0):
        self._current += 1
        self.max_in_flight = max(self.max_in_flight, self._current)
        try:
            await asyncio.sleep(0.01)
            return await self._real.candidates(subject, minimum_score=minimum_score)
        finally:
            self._current -= 1


class TrackingAdjudicator:
    """A `MergeAdjudicator` that confirms everything and tracks concurrency
    of `adjudicate_many` calls -- the phase that actually talks to the
    endpoint, unlike `TrackingFinder` above."""

    def __init__(self) -> None:
        self._current = 0
        self.max_in_flight = 0
        self.calls = 0

    async def adjudicate(self, subject, candidates):
        raise NotImplementedError("resolve_many must call adjudicate_many, not adjudicate")

    async def adjudicate_many(self, work):
        self._current += 1
        self.max_in_flight = max(self.max_in_flight, self._current)
        self.calls += 1
        try:
            await asyncio.sleep(0.02)
            return [[_true_verdict() for _ in candidates] for _subject, candidates in work]
        finally:
            self._current -= 1


def _true_verdict():
    from redstring.consolidation.policy import AdjudicationVerdict

    return AdjudicationVerdict(same=True, confidence=0.9, reason="tracked")


class TestThePhaseBarrier:
    async def test_every_subject_is_scored_before_any_merge_is_emitted(self):
        """The barrier, asserted directly rather than inferred from results.

        Three independent groups (a subject and its own exact-name
        duplicate, no cross-group interaction), scored concurrently. If
        phases interleaved, some group's write could land before another
        group's score finished; the phase split forbids that entirely.
        """
        rig, tenant = Rig(), uid(999)
        subjects = []
        for i in range(1, 4):
            subject = keyed(tenant, DISTINCT_NAMES[i - 1], entity_id=uid(i))
            duplicate = keyed(tenant, DISTINCT_NAMES[i - 1], entity_id=uid(10 + i))
            await rig.seed(subject, duplicate)
            subjects.append(subject)

        finder = RecordingFinder(rig.finder)
        writes: list[float] = []
        original_append = rig.event_store.append

        async def recording_append(*args, **kwargs):
            result = await original_append(*args, **kwargs)
            writes.append(asyncio.get_running_loop().time())
            return result

        rig.event_store.append = recording_append

        events = await rig.service.resolve_many(subjects, finder=finder, concurrency=3)

        assert len(events) == 3
        assert finder.finished_at, "the finder was never called"
        assert writes, "no merge was ever written"
        assert max(finder.finished_at) < min(writes), (
            "a write happened before every subject had been scored -- "
            "phase 1 and phase 3 interleaved"
        )


class TestMergingTheSameCandidateFromTwoSubjects:
    async def test_a_candidate_confirmed_by_two_subjects_is_merged_exactly_once(self):
        """First emit wins; the second finds its own subject already an
        alias, so its whole decision is skipped.

        Three "Ada Lovelace" entities, two of them (`a`, `b`) subjects and
        one (`dup`) not. All three block and score high against each other,
        so `a`'s confirmed list is `{b, dup}` and `b`'s is `{a, dup}` -- a
        repeated mention is exactly what makes a corpus pass need this
        skip, not an exotic case.
        """
        rig, tenant = Rig(), uid(999)
        a = keyed(tenant, "Ada Lovelace", entity_id=uid(1))
        b = keyed(tenant, "Ada Lovelace", entity_id=uid(2))
        dup = keyed(tenant, "Ada Lovelace", entity_id=uid(3))
        await rig.seed(a, b, dup)

        events = await rig.service.resolve_many([a, b], finder=rig.finder, concurrency=2)

        assert len(events) == 1
        assert events[0].canonical_entity_id == a.id
        assert set(events[0].merged_entity_ids) == {b.id, dup.id}

        await rig.catch_up()
        from redstring.events.merge import EntitiesMerged

        assert len([e for e in await rig.events() if isinstance(e, EntitiesMerged)]) == 1


class TestASubjectMergedAwayEarlierInThePass:
    async def test_a_subject_merged_away_earlier_in_the_pass_is_skipped(self):
        """The mutual case: A confirms B and B confirms A.

        With a symmetric scorer this is the normal outcome for a genuine
        duplicate pair when both are in the subject list -- exactly two
        entities, each the other's only candidate. The second decision must
        be dropped, not retried and not raised.
        """
        rig, tenant = Rig(), uid(999)
        a = keyed(tenant, "Ada Lovelace", entity_id=uid(1))
        b = keyed(tenant, "Ada Lovelace", entity_id=uid(2))
        await rig.seed(a, b)

        events = await rig.service.resolve_many([a, b], finder=rig.finder, concurrency=2)

        assert len(events) == 1
        assert events[0].canonical_entity_id == a.id
        assert events[0].merged_entity_ids == [b.id]


class TestEmitOrderIsDeterministic:
    async def test_the_emit_order_is_deterministic(self):
        """Two runs over one graph agree, whatever order phase 1 completed
        in. Compared by the sequence of canonical ids the run produced, not
        by inspecting the sort key -- that would test the implementation
        rather than the claim."""

        async def run_once():
            rig, tenant = Rig(), uid(999)
            subjects = []
            for i in range(1, 5):
                subject = keyed(tenant, DISTINCT_NAMES[i - 1], entity_id=uid(i))
                duplicate = keyed(tenant, DISTINCT_NAMES[i - 1], entity_id=uid(10 + i))
                await rig.seed(subject, duplicate)
                subjects.append(subject)
            events = await rig.service.resolve_many(subjects, finder=rig.finder, concurrency=4)
            return [event.canonical_entity_id for event in events]

        first = await run_once()
        second = await run_once()

        assert first == second
        assert len(first) == 4


class TestConcurrencyOneMatchesTheSerialLoop:
    async def test_concurrency_one_produces_the_same_merges_as_calling_resolve_in_a_loop(self):
        """The equivalence that makes the default safe.

        The oracle is a hand-written serial loop over `resolve`, built
        independently of `resolve_many` -- not `resolve_many` with a
        different argument. A round-trip whose two sides share the code
        under test would check determinism, not correctness.
        """

        def build_subjects(tenant):
            return [keyed(tenant, DISTINCT_NAMES[i - 1], entity_id=uid(i)) for i in range(1, 4)]

        # resolve_many side.
        rig_a, tenant_a = Rig(), uid(111)
        subjects_a = build_subjects(tenant_a)
        for i, subject in zip(range(1, 4), subjects_a, strict=True):
            duplicate = keyed(tenant_a, DISTINCT_NAMES[i - 1], entity_id=uid(10 + i))
            await rig_a.seed(subject, duplicate)
        via_resolve_many = await rig_a.service.resolve_many(
            subjects_a, finder=rig_a.finder, concurrency=1
        )

        # Independent oracle: a plain serial loop over `resolve`.
        rig_b, tenant_b = Rig(), uid(111)
        subjects_b = build_subjects(tenant_b)
        for i, subject in zip(range(1, 4), subjects_b, strict=True):
            duplicate = keyed(tenant_b, DISTINCT_NAMES[i - 1], entity_id=uid(10 + i))
            await rig_b.seed(subject, duplicate)
        via_loop = []
        for subject in subjects_b:
            event = await rig_b.service.resolve(subject, finder=rig_b.finder)
            if event is not None:
                via_loop.append(event)

        assert [e.canonical_entity_id for e in via_resolve_many] == [
            e.canonical_entity_id for e in via_loop
        ]
        assert [set(e.merged_entity_ids) for e in via_resolve_many] == [
            set(e.merged_entity_ids) for e in via_loop
        ]


class TestTheConcurrencyBound:
    async def test_no_more_than_concurrency_scorings_are_in_flight_at_once(self):
        """The bound, asserted by a finder that records its own high-water mark."""
        rig, tenant = Rig(), uid(999)
        subjects = [keyed(tenant, DISTINCT_NAMES[i - 1], entity_id=uid(i)) for i in range(1, 6)]
        for subject in subjects:
            await rig.seed(subject)
        tracker = TrackingFinder(rig.finder)

        events = await rig.service.resolve_many(subjects, finder=tracker, concurrency=2)

        assert events == []
        assert tracker.max_in_flight <= 2
        assert tracker.max_in_flight >= 2, "the concurrency bound was never actually exercised"

    async def test_no_more_than_concurrency_model_calls_are_in_flight_at_once(self):
        """The limiter, on the phase that actually talks to the endpoint.

        Phase 1 makes no model calls at all, so a test bounding phase 1
        would pass against an implementation with no limiter in it. Two
        separate `resolve_many` calls, on separate tenants so they cannot
        collide on the log's optimistic concurrency, share one
        `CallLimiter(1)` -- proving the bound is enforced across callers,
        which is the whole reason it is an object rather than a number.
        """
        limiter = CallLimiter(1)
        tracker = TrackingAdjudicator()

        async def one_pass(tenant_seed: int):
            rig, tenant = Rig(), uid(tenant_seed)
            subject = keyed(tenant, "Ada Lovelace", entity_id=uid(1))
            ambiguous = keyed(tenant, "Ada Lovegood", entity_id=uid(2))
            await rig.seed(subject, ambiguous)
            return await rig.service.resolve_many(
                [subject], finder=rig.finder, adjudicator=tracker, concurrency=4, limiter=limiter
            )

        results = await asyncio.gather(one_pass(1001), one_pass(1002))

        assert tracker.calls == 2
        assert tracker.max_in_flight <= 1
        assert all(len(events) == 1 for events in results)


class TestNoCandidatesAndEmptyInput:
    async def test_a_subject_with_no_candidates_contributes_no_event(self):
        """`None` from `_score_and_band` must not become an empty merge."""
        rig, tenant = Rig(), uid(999)
        subject = keyed(tenant, "Ada Lovelace", entity_id=uid(1))
        await rig.seed(subject)

        events = await rig.service.resolve_many([subject], finder=rig.finder)

        assert events == []

    async def test_an_empty_subject_list_makes_no_calls_and_returns_empty(self):
        rig = Rig()
        finder = RecordingFinder(rig.finder)

        events = await rig.service.resolve_many([], finder=finder)

        assert events == []
        assert finder.finished_at == []
