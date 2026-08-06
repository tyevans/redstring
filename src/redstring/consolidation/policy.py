"""Two thresholds, and the band between them that costs a model call.

BACKLOG **B40**: slice 6 deleted `SimpleMerger`/`LLMMerger` rather than porting
them, because they resolved entities *inside extraction* -- invisibly, with no
`EntitiesMerged` to audit or undo, bypassing `ConsolidationLog`'s three
invariants. The policy they implemented was sound and its thresholds were
tuned, so it is rebuilt here, where a decision becomes an event.

```
score >= HIGH   ->  MERGE       no model call
LOW <= s < HIGH ->  ADJUDICATE  one model call, batched
score <  LOW    ->  REJECT      no model call
```

**The band is the whole design.** Sending every blocked pair to a model is
quadratic in model calls, which is the cost that makes LLM-assisted resolution
impractical; sending only the band makes it proportional to the genuinely
ambiguous pairs, which is a small fraction of a block. Widening the band is
therefore a spend decision, and it is stated in two constants rather than
buried in a condition.

## Within-document resolution is not a special case

`extraction.mapping.entity_id_for` namespaces ids **per document**, so `doc-1`'s
"Ada" and `doc-2`'s "Ada" are two entities by construction. Two mentions in the
*same* document that extraction could not unify -- "Ada Lovelace" and "Ada" --
are likewise two entities, and reach this code by the same path with both
happening to share a `source_id`. There is no within-document branch here, and
adding one would be reintroducing the thing B40 records as deleted.

## The model is asked a question it can decline

`AdjudicationVerdict` has a `same` boolean and a `confidence`. A provider that
raises -- `EmptyCompletionError`, `RefusedCompletionError` -- leaves the pair
`ADJUDICATE` rather than resolving it either way: an unanswered question is not
a "no". Deciding "no" on a provider error would make an outage look like a
corpus with no duplicates in it, which is exactly the failure `FeatureWeights`
refuses all-zero weights to avoid.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from redstring.domain.exceptions import LlmProviderError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.consolidation.candidates import ScoredCandidate
    from redstring.domain.entity import Entity
    from redstring.ports.llm_provider import LlmProvider

#: At or above this, merge without asking a model.
#:
#: Inherited from `MERGER_HIGH_SIMILARITY_THRESHOLD`, where it was tuned. Named
#: constants rather than defaults on a function, because the two values are
#: only meaningful relative to each other and a caller overriding one of them
#: is the way the band silently disappears.
HIGH_SIMILARITY = 0.92

#: Below this, never merge and never ask.
LOW_SIMILARITY = 0.75


class MergeDecision(StrEnum):
    """What the policy says to do with one candidate pair."""

    MERGE = "merge"
    ADJUDICATE = "adjudicate"
    REJECT = "reject"


def decide(
    score: float, *, high: float = HIGH_SIMILARITY, low: float = LOW_SIMILARITY
) -> MergeDecision:
    """Which band `score` falls in.

    Both bounds are inclusive-from-below: `score == high` merges and
    `score == low` adjudicates. Stated because a threshold's boundary is
    exactly where an off-by-one hides, and a pair scoring precisely the
    threshold is not rare -- an exact name match with no other signal produces
    a round number every time.

    Raises `ValueError` if `low > high`. An inverted pair does not fail, it
    quietly makes the band empty and every pair either merge or reject, which
    reads as "the model is never needed" rather than as a misconfiguration.
    """
    if low > high:
        raise ValueError(
            f"low ({low}) must not exceed high ({high}); an inverted band silently "
            f"disables adjudication rather than failing"
        )
    if score >= high:
        return MergeDecision.MERGE
    if score >= low:
        return MergeDecision.ADJUDICATE
    return MergeDecision.REJECT


class AdjudicationQuestion(BaseModel):
    """One pair put to a model, in the model's terms rather than the graph's."""

    left: str
    right: str
    entity_type: str
    left_description: str | None = None
    right_description: str | None = None


class AdjudicationVerdict(BaseModel):
    """What the model said about one pair.

    `reason` is required and free-form. It is not decoration: it lands on
    `EntitiesMerged.merge_reason` and is the only record of *why* a judgement
    call went the way it did, which is the whole difference between this and
    the unaudited resolution B40 describes.
    """

    same: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class AdjudicationBatch(BaseModel):
    """Verdicts for a batch of pairs, in the order they were asked.

    A list rather than a mapping keyed by id, because the ids are the graph's
    business and putting them in a prompt invites a model to invent one. The
    caller re-pairs by position, and a batch whose length disagrees is
    rejected -- see `adjudicate`.
    """

    verdicts: list[AdjudicationVerdict]


#: How many pairs go into one model call.
#:
#: Inherited from `MERGER_LLM_BATCH_SIZE`. Batching is what keeps the band
#: affordable; the ceiling exists because a long batch is where a model starts
#: losing track of which answer belongs to which pair, and position is how the
#: answers are re-paired.
ADJUDICATION_BATCH_SIZE = 10

_SYSTEM_PROMPT = (
    "You decide whether two extracted entity mentions refer to the same "
    "real-world thing. Answer one verdict per pair, in the order given, and "
    "return exactly as many verdicts as there are pairs. Say `same: false` "
    "when you are unsure; a wrong merge is harder to notice than a missed "
    "one. Give a short reason for every verdict."
)


class Adjudicator:
    """Puts the ambiguous band to a model, in batches."""

    def __init__(self, provider: LlmProvider, *, batch_size: int = ADJUDICATION_BATCH_SIZE) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._provider = provider
        self._batch_size = batch_size

    async def adjudicate(
        self, subject: Entity, candidates: Sequence[ScoredCandidate]
    ) -> list[AdjudicationVerdict | None]:
        """One verdict per candidate, `None` where the model did not answer.

        `None` rather than a fabricated "not the same": a provider error and a
        model saying no are different facts, and collapsing them turns an
        outage into a corpus that appears to hold no duplicates.

        A batch whose verdict count disagrees with the pair count yields `None`
        for **every** pair in that batch, not for the tail. A short answer
        means the alignment between question and verdict is unknown, so the
        verdicts that did arrive cannot be trusted to belong to the pairs they
        line up with -- and silently taking the prefix is how a model's answer
        about pair 3 gets recorded against pair 1.
        """
        verdicts: list[AdjudicationVerdict | None] = []
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            verdicts.extend(await self._one_batch(subject, batch))
        return verdicts

    async def _one_batch(
        self, subject: Entity, batch: Sequence[ScoredCandidate]
    ) -> list[AdjudicationVerdict | None]:
        questions = [
            AdjudicationQuestion(
                left=subject.name,
                right=candidate.entity.name,
                entity_type=subject.entity_type,
                left_description=subject.description,
                right_description=candidate.entity.description,
            )
            for candidate in batch
        ]
        try:
            answer = await self._provider.extract(
                _render(questions), AdjudicationBatch, system_prompt=_SYSTEM_PROMPT
            )
        except LlmProviderError:
            return [None] * len(batch)

        try:
            # `zip(strict=True)` rather than comparing two `len()`s, and the
            # difference is not style. CPython interns small ints, so
            # `len(a) != len(b)` and `len(a) is not len(b)` agree for every
            # batch any test uses and disagree above 256 -- a cosmic-ray
            # mutant rewriting `!=` as `is not` survived this file's whole
            # suite. `EntitiesMerged._the_merge_is_coherent` hit the same trap
            # and sidestepped it the same way: express the check so no int
            # comparison exists to get wrong.
            return [verdict for _, verdict in zip(batch, answer.verdicts, strict=True)]
        except ValueError:
            return [None] * len(batch)


def _render(questions: Sequence[AdjudicationQuestion]) -> str:
    """The batch as text. Numbered, because the answers re-pair by position."""
    lines = []
    for index, question in enumerate(questions, start=1):
        lines.append(f"Pair {index} (type: {question.entity_type}):")
        lines.append(f"  A: {question.left}")
        if question.left_description:
            lines.append(f"     {question.left_description}")
        lines.append(f"  B: {question.right}")
        if question.right_description:
            lines.append(f"     {question.right_description}")
    return "\n".join(lines)
