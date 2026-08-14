"""Batches that span subjects, and the position mapping that makes them safe.

`Adjudicator.adjudicate` batches within one subject, so a subject with two
ambiguous candidates spends a whole model call on two pairs.
`adjudicate_many` fills each batch from as many subjects as it takes.

The mapping from a batch position back to `(subject, candidate)` is the whole
risk of this feature -- `AdjudicationBatch` deliberately keeps ids out of the
prompt, so position is the only thing tying an answer to its question, and a
batch that spans subjects makes that mapping non-trivial for the first time.
"""

from __future__ import annotations

from uuid import UUID

from redstring.consolidation.candidates import ScoredCandidate
from redstring.consolidation.policy import (
    ADJUDICATION_BATCH_SIZE,
    AdjudicationBatch,
    AdjudicationVerdict,
    Adjudicator,
)
from redstring.domain.exceptions import LlmProviderError
from redstring.domain.similarity import SimilarityFeatures

from .conftest import entity

_TENANT = UUID(int=1)

subject_a = entity(_TENANT, name="Subject A", entity_id=UUID(int=10), source_id="doc-a")
subject_b = entity(_TENANT, name="Subject B", entity_id=UUID(int=11), source_id="doc-b")
subject_c = entity(_TENANT, name="Subject C", entity_id=UUID(int=12), source_id="doc-c")


def candidate(i: int) -> ScoredCandidate:
    """A `ScoredCandidate` with a pinned entity id derived from `i`."""
    return ScoredCandidate(
        entity=entity(_TENANT, name=f"Candidate {i}", entity_id=UUID(int=1000 + i)),
        features=SimilarityFeatures(name=0.8),
        score=0.8,
    )


c1, c2, c3, c4, c5, c6 = (candidate(i) for i in range(1, 7))


class RecordingProvider:
    """An `LlmProvider` that answers every pair and records what it was asked.

    Each verdict's `reason` is the pair's *global* index across all calls, so
    a test can assert exactly which question each answer came back for. A
    provider returning uniform verdicts could not distinguish a correct
    mapping from a shifted one.
    """

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self._answered = 0

    async def extract(self, prompt, schema, *, system_prompt=None):
        self.prompts.append(prompt)
        pair_count = prompt.count("Pair ")
        verdicts = []
        for _ in range(pair_count):
            verdicts.append(
                AdjudicationVerdict(same=True, confidence=1.0, reason=f"q{self._answered}")
            )
            self._answered += 1
        return AdjudicationBatch(verdicts=verdicts)


class ShortAnsweringProvider:
    """Answers every call with fewer verdicts than pairs asked."""

    def __init__(self, *, short_by: int) -> None:
        self._short_by = short_by

    async def extract(self, prompt, schema, *, system_prompt=None):
        pair_count = prompt.count("Pair ")
        count = max(0, pair_count - self._short_by)
        verdicts = [
            AdjudicationVerdict(same=True, confidence=1.0, reason=f"q{i}") for i in range(count)
        ]
        return AdjudicationBatch(verdicts=verdicts)


class FailOnCallProvider:
    """Raises `LlmProviderError` on the nth call (0-indexed); answers every other call."""

    def __init__(self, *, fail_on: int) -> None:
        self._fail_on = fail_on
        self._calls = 0

    async def extract(self, prompt, schema, *, system_prompt=None):
        call = self._calls
        self._calls += 1
        if call == self._fail_on:
            raise LlmProviderError("provider unavailable", model="fake/adjudicator-1")
        pair_count = prompt.count("Pair ")
        verdicts = [
            AdjudicationVerdict(same=True, confidence=1.0, reason=f"q{i}")
            for i in range(pair_count)
        ]
        return AdjudicationBatch(verdicts=verdicts)


async def test_one_call_covers_pairs_from_several_subjects():
    """Three subjects with two pairs each is one call, not three."""
    provider = RecordingProvider()
    adjudicator = Adjudicator(provider)
    work = [(subject_a, [c1, c2]), (subject_b, [c3, c4]), (subject_c, [c5, c6])]

    await adjudicator.adjudicate_many(work)

    assert len(provider.prompts) == 1


async def test_each_subject_gets_back_verdicts_for_its_own_candidates():
    """The mapping test. Reasons carry the global question index.

    Subject A asked questions 0 and 1, subject B questions 2 and 3. A
    mapping that reset per subject, or that sliced the flat answer list at
    the wrong offsets, returns a different assignment here -- which is the
    defect this whole module exists to catch.
    """
    provider = RecordingProvider()
    adjudicator = Adjudicator(provider)

    results = await adjudicator.adjudicate_many([(subject_a, [c1, c2]), (subject_b, [c3, c4])])

    assert [v.reason for v in results[0]] == ["q0", "q1"]
    assert [v.reason for v in results[1]] == ["q2", "q3"]


async def test_a_subject_whose_pairs_straddle_a_batch_boundary_still_re_pairs():
    """The case a per-subject batcher never produces.

    One subject's candidates are split across two model calls. Its verdict
    list must still be its own candidates in order, reassembled from two
    responses -- and the offsets differ per call, which is where an
    off-by-one lives.
    """
    provider = RecordingProvider()
    adjudicator = Adjudicator(provider)
    # `ADJUDICATION_BATCH_SIZE` pairs on the first subject fills batch one
    # exactly; the second subject's pairs open batch two.
    first = [candidate(i) for i in range(ADJUDICATION_BATCH_SIZE - 1)]
    second = [candidate(100), candidate(101)]

    results = await adjudicator.adjudicate_many([(subject_a, first), (subject_b, second)])

    assert len(provider.prompts) == 2
    assert len(results[0]) == len(first)
    assert len(results[1]) == len(second)
    assert [v.reason for v in results[1]] == ["q9", "q10"]


async def test_a_short_batch_yields_none_for_every_subject_it_touched():
    """The existing safety property, extended across the boundary.

    `adjudicate` already yields `None` for every pair in a short batch rather
    than for the tail, because alignment is unknown once the count disagrees.
    A batch spanning two subjects must poison **both**, not just the one whose
    pairs happened to come last.
    """
    provider = ShortAnsweringProvider(short_by=1)
    adjudicator = Adjudicator(provider)

    results = await adjudicator.adjudicate_many([(subject_a, [c1]), (subject_b, [c2])])

    assert results == [[None], [None]]


async def test_a_subject_with_no_candidates_gets_an_empty_list_not_a_dropped_slot():
    """Alignment is by position in `work`, so an empty subject must hold its place."""
    provider = RecordingProvider()
    adjudicator = Adjudicator(provider)

    results = await adjudicator.adjudicate_many([(subject_a, []), (subject_b, [c1])])

    assert results[0] == []
    assert [v.reason for v in results[1]] == ["q0"]


async def test_a_provider_error_yields_none_only_for_the_batch_that_failed():
    """One failed call must not poison subjects whose pairs were in other calls."""
    provider = FailOnCallProvider(fail_on=0)
    adjudicator = Adjudicator(provider)
    first = [candidate(i) for i in range(ADJUDICATION_BATCH_SIZE)]
    second = [candidate(100)]

    results = await adjudicator.adjudicate_many([(subject_a, first), (subject_b, second)])

    assert results[0] == [None] * ADJUDICATION_BATCH_SIZE
    assert results[1][0] is not None
