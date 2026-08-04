"""Two thresholds, the band between them, and what a model is asked.

The provider here is a **fake**, not a mock: a real class implementing
`LlmProvider`, returning values a real provider could return. The distinction
matters because the interesting cases are a provider that *raises* and a
provider that answers with the wrong number of verdicts, and a mock asserting
"was called once" would notice neither.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kg_builder.consolidation.candidates import ScoredCandidate
from kg_builder.consolidation.policy import (
    ADJUDICATION_BATCH_SIZE,
    HIGH_SIMILARITY,
    LOW_SIMILARITY,
    AdjudicationBatch,
    AdjudicationVerdict,
    Adjudicator,
    MergeDecision,
    decide,
)
from kg_builder.domain.exceptions import (
    EmptyCompletionError,
    RefusedCompletionError,
)
from kg_builder.domain.similarity import SimilarityFeatures
from kg_builder.ports.llm_provider import LlmProvider

from .conftest import entity


class FakeProvider:
    """Answers with whatever it was built holding. Records what it was asked."""

    def __init__(self, *, answers=None, raises=None) -> None:
        self._answers = list(answers or [])
        self._raises = raises
        self.prompts: list[str] = []

    @property
    def model(self) -> str:
        return "fake/adjudicator-1"

    async def extract(self, text, schema, *, system_prompt=None):
        self.prompts.append(text)
        if self._raises is not None:
            raise self._raises
        return self._answers.pop(0)


def _candidate(tenant_id, name, score=0.8):
    return ScoredCandidate(
        entity=entity(tenant_id, name=name),
        features=SimilarityFeatures(name=score),
        score=score,
    )


def _verdict(same=True, confidence=0.9, reason="same person"):
    return AdjudicationVerdict(same=same, confidence=confidence, reason=reason)


class TestTheBands:
    def test_a_high_score_merges_without_asking(self):
        assert decide(0.99) is MergeDecision.MERGE

    def test_a_middling_score_goes_to_the_model(self):
        assert decide(0.80) is MergeDecision.ADJUDICATE

    def test_a_low_score_is_rejected_without_asking(self):
        assert decide(0.10) is MergeDecision.REJECT

    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (HIGH_SIMILARITY, MergeDecision.MERGE),
            (LOW_SIMILARITY, MergeDecision.ADJUDICATE),
        ],
    )
    def test_both_bounds_are_inclusive_from_below(self, score, expected):
        """The boundary is exactly where an off-by-one hides, and a pair
        landing precisely on a threshold is not rare -- an exact name match
        with no other signal produces a round number every time."""
        assert decide(score) is expected

    def test_just_below_each_bound_falls_into_the_lower_band(self):
        assert decide(HIGH_SIMILARITY - 1e-9) is MergeDecision.ADJUDICATE
        assert decide(LOW_SIMILARITY - 1e-9) is MergeDecision.REJECT

    def test_an_inverted_band_is_refused(self):
        """It does not fail, it quietly empties the band -- every pair merges
        or rejects and the model is never called, which reads as "adjudication
        was not needed" rather than as a misconfiguration."""
        with pytest.raises(ValueError, match="inverted band"):
            decide(0.5, high=0.2, low=0.8)

    def test_an_empty_band_is_allowed(self):
        """`low == high` is a deliberate configuration -- adjudication off --
        and is not the same mistake as inverting them."""
        assert decide(0.5, high=0.5, low=0.5) is MergeDecision.MERGE
        assert decide(0.4, high=0.5, low=0.5) is MergeDecision.REJECT

    def test_the_default_band_is_not_empty(self):
        """Guards the two constants against being edited to meet."""
        assert LOW_SIMILARITY < HIGH_SIMILARITY

    @given(score=st.floats(0.0, 1.0))
    def test_every_score_falls_in_exactly_one_band(self, score):
        assert decide(score) in set(MergeDecision)

    @given(low=st.floats(0.0, 1.0), high=st.floats(0.0, 1.0), score=st.floats(0.0, 1.0))
    def test_the_bands_are_monotone_in_the_score(self, low, high, score):
        """A higher score never yields a *less* willing decision. A sign error
        anywhere in the comparison chain breaks this and nothing else."""
        if low > high:
            low, high = high, low
        order = {MergeDecision.REJECT: 0, MergeDecision.ADJUDICATE: 1, MergeDecision.MERGE: 2}
        higher = min(1.0, score + 0.05)

        assert order[decide(score, high=high, low=low)] <= order[decide(higher, high=high, low=low)]


class TestAdjudication:
    async def test_a_verdict_comes_back_per_candidate(self):
        tenant = uuid4()
        subject = entity(tenant, name="Ada Lovelace")
        candidates = [_candidate(tenant, "Ada"), _candidate(tenant, "A. Lovelace")]
        provider = FakeProvider(
            answers=[AdjudicationBatch(verdicts=[_verdict(True), _verdict(False)])]
        )

        verdicts = await Adjudicator(provider).adjudicate(subject, candidates)

        assert [v.same for v in verdicts] == [True, False]

    async def test_the_provider_satisfies_the_port(self):
        """Guards the fake: a stub that had drifted from `LlmProvider` would
        make every test here pass against an interface nothing implements."""
        assert isinstance(FakeProvider(), LlmProvider)

    async def test_pairs_are_batched(self):
        tenant = uuid4()
        subject = entity(tenant, name="Ada Lovelace")
        candidates = [_candidate(tenant, f"Ada {index}") for index in range(5)]
        provider = FakeProvider(
            answers=[
                AdjudicationBatch(verdicts=[_verdict() for _ in range(2)]),
                AdjudicationBatch(verdicts=[_verdict() for _ in range(2)]),
                AdjudicationBatch(verdicts=[_verdict()]),
            ]
        )

        verdicts = await Adjudicator(provider, batch_size=2).adjudicate(subject, candidates)

        assert len(provider.prompts) == 3
        assert len(verdicts) == 5

    async def test_the_batch_size_bounds_the_call_count(self):
        """Batching is what keeps the band affordable. One call per pair is
        the cost that made LLM-assisted resolution impractical."""
        tenant = uuid4()
        subject = entity(tenant, name="Ada Lovelace")
        candidates = [_candidate(tenant, f"Ada {index}") for index in range(20)]
        provider = FakeProvider(
            answers=[AdjudicationBatch(verdicts=[_verdict() for _ in range(10)]) for _ in range(2)]
        )

        await Adjudicator(provider).adjudicate(subject, candidates)

        assert len(provider.prompts) == 20 // ADJUDICATION_BATCH_SIZE

    async def test_the_prompt_names_both_sides_and_the_type(self):
        tenant = uuid4()
        subject = entity(tenant, name="Ada Lovelace", description="a mathematician")
        candidates = [_candidate(tenant, "A. Lovelace")]
        provider = FakeProvider(answers=[AdjudicationBatch(verdicts=[_verdict()])])

        await Adjudicator(provider).adjudicate(subject, candidates)

        [prompt] = provider.prompts
        assert "Ada Lovelace" in prompt
        assert "A. Lovelace" in prompt
        assert "person" in prompt
        assert "a mathematician" in prompt

    async def test_the_pairs_are_numbered_from_one(self):
        """The numbering is how a reader of the prompt lines an answer up with
        a question, and the system prompt asks for verdicts "in the order
        given". Starting at 0 or 2 would be a prompt whose numbering disagreed
        with the ordinary reading of "the first pair" -- pinned because
        cosmic-ray found nothing asserting it."""
        tenant = uuid4()
        candidates = [_candidate(tenant, f"Ada {index}") for index in range(3)]
        provider = FakeProvider(
            answers=[AdjudicationBatch(verdicts=[_verdict() for _ in range(3)])]
        )

        await Adjudicator(provider).adjudicate(entity(tenant), candidates)

        [prompt] = provider.prompts
        assert "Pair 1 " in prompt
        assert "Pair 3 " in prompt
        assert "Pair 0 " not in prompt
        assert "Pair 4 " not in prompt

    async def test_the_prompt_never_carries_an_entity_id(self):
        """Ids are the graph's business. Putting one in a prompt invites a
        model to echo it back, and an invented id is a merge of something that
        does not exist."""
        tenant = uuid4()
        subject = entity(tenant, name="Ada Lovelace")
        candidates = [_candidate(tenant, "A. Lovelace")]
        provider = FakeProvider(answers=[AdjudicationBatch(verdicts=[_verdict()])])

        await Adjudicator(provider).adjudicate(subject, candidates)

        [prompt] = provider.prompts
        for identifier in (subject.id, candidates[0].entity.id, tenant):
            assert str(identifier) not in prompt

    async def test_nothing_is_asked_when_there_are_no_candidates(self):
        provider = FakeProvider()

        verdicts = await Adjudicator(provider).adjudicate(entity(uuid4()), [])

        assert verdicts == []
        assert provider.prompts == []

    async def test_a_batch_size_of_one_is_legal(self):
        """The boundary the guard sits on. `batch_size=1` is a real
        configuration -- one pair per call, the most expensive and most
        reliable setting -- and a `<= 1` guard would reject it while still
        rejecting zero, so nothing else here would notice. cosmic-ray found
        that `<` rewritten as `<=` survived."""
        tenant = uuid4()
        candidates = [_candidate(tenant, "Ada"), _candidate(tenant, "A. Lovelace")]
        provider = FakeProvider(
            answers=[
                AdjudicationBatch(verdicts=[_verdict(True)]),
                AdjudicationBatch(verdicts=[_verdict(False)]),
            ]
        )

        verdicts = await Adjudicator(provider, batch_size=1).adjudicate(entity(tenant), candidates)

        assert len(provider.prompts) == 2
        assert [v.same for v in verdicts] == [True, False]

    async def test_a_batch_size_below_one_is_refused(self):
        """Zero would make the batching loop produce no calls and no verdicts
        for any input, which reads as a model that answered nothing."""
        with pytest.raises(ValueError, match="at least 1"):
            Adjudicator(FakeProvider(), batch_size=0)


class TestWhenTheModelDoesNotAnswer:
    @pytest.mark.parametrize(
        "error",
        [
            EmptyCompletionError(model="fake/adjudicator-1", finish_reason="length"),
            RefusedCompletionError(model="fake/adjudicator-1"),
        ],
    )
    async def test_a_provider_error_leaves_the_pair_undecided(self, error):
        """`None`, not a fabricated "not the same". An outage that read as a
        string of rejections would look exactly like a corpus with no
        duplicates in it, which is a plausible answer nobody investigates."""
        tenant = uuid4()
        candidates = [_candidate(tenant, "Ada"), _candidate(tenant, "A. Lovelace")]

        verdicts = await Adjudicator(FakeProvider(raises=error)).adjudicate(
            entity(tenant), candidates
        )

        assert verdicts == [None, None]

    async def test_a_short_answer_invalidates_its_whole_batch(self):
        """Not just the tail. The verdicts re-pair by *position*, so a batch
        that came back short means the alignment is unknown -- taking the
        prefix is how a model's answer about pair 3 is recorded against pair 1.
        """
        tenant = uuid4()
        candidates = [_candidate(tenant, f"Ada {index}") for index in range(3)]
        provider = FakeProvider(
            answers=[AdjudicationBatch(verdicts=[_verdict(True), _verdict(True)])]
        )

        verdicts = await Adjudicator(provider).adjudicate(entity(tenant), candidates)

        assert verdicts == [None, None, None]

    async def test_a_long_answer_invalidates_its_whole_batch_too(self):
        tenant = uuid4()
        candidates = [_candidate(tenant, "Ada")]
        provider = FakeProvider(answers=[AdjudicationBatch(verdicts=[_verdict(), _verdict()])])

        verdicts = await Adjudicator(provider).adjudicate(entity(tenant), candidates)

        assert verdicts == [None]

    async def test_one_bad_batch_does_not_poison_the_others(self):
        """The batches are independent questions; a model losing track in one
        says nothing about the next."""
        tenant = uuid4()
        candidates = [_candidate(tenant, f"Ada {index}") for index in range(4)]
        provider = FakeProvider(
            answers=[
                AdjudicationBatch(verdicts=[_verdict(True)]),  # short: batch of 2
                AdjudicationBatch(verdicts=[_verdict(True), _verdict(False)]),
            ]
        )

        verdicts = await Adjudicator(provider, batch_size=2).adjudicate(entity(tenant), candidates)

        assert verdicts[:2] == [None, None]
        assert [v.same for v in verdicts[2:]] == [True, False]


class TestTheVerdictShape:
    def test_a_verdict_must_carry_a_reason(self):
        """It lands on `EntitiesMerged.merge_reason` and is the only record of
        why a judgement call went the way it did -- which is the whole
        difference between this and the unaudited resolution B40 describes."""
        with pytest.raises(ValueError, match="reason"):
            AdjudicationVerdict(same=True, confidence=0.9)

    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_confidence_is_bounded(self, confidence):
        with pytest.raises(ValueError, match="confidence"):
            AdjudicationVerdict(same=True, confidence=confidence, reason="x")
