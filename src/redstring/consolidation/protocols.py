"""The two shapes consolidation plugs in: `CandidateSource` and `MergeAdjudicator`.

`Consolidator.resolve` has always invited substitution -- "supply one to
change the weights or the blocking" -- but its parameters were annotated
against the *concrete* `CandidateFinder` and `Adjudicator`. Inviting a
substitution and typing it against an implementation are different promises,
and the gap was paid by the caller: replacing the blocking with your own
search index meant subclassing a class whose `__init__` demands a
`GraphStore`, an optional `VectorStore`, a `FeatureWeights` and a flag, then
overriding the one method you actually wanted. A human review queue standing
in for the model meant subclassing a class that requires an `LlmProvider` to
construct and never calling it.

Both are single-method interfaces, which is what makes the protocols cheap
enough to be worth having. The concrete classes remain the defaults and are
unchanged; they satisfy these structurally, with no base class and no
registration.

## Why these live here and not in `ports/`

`ports/` sits below the sibling band, and both protocols traffic in
`ScoredCandidate`, which is a consolidation type. A port for either would
have to import upward. That is the layering telling the truth about what
these are: not store or provider boundaries the whole library is built on,
but the two decisions *within* consolidation that a caller might reasonably
own -- which candidates to consider, and who settles the ambiguous ones.

## What a substitute owes

`CandidateSource.candidates` must return its results **best first, under a
total order**. `CandidateFinder` breaks score ties by ascending entity id as
a string precisely so that two runs over one graph agree; a substitute that
sorts by score alone leaves `resolve`'s banding to depend on whatever order
its own backend happened to return, which is a difference that shows up as
an intermittently different merge rather than as an error.

`MergeAdjudicator.adjudicate` must return **exactly one verdict per candidate,
positionally aligned**, and `None` where it has no answer. `None` is not a
formality: a provider outage and a considered "not the same" are different
facts, and collapsing them turns an outage into a corpus that appears to hold
no duplicates. A substitute that cannot answer for a pair says `None` rather
than `False`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.consolidation.candidates import ScoredCandidate
    from redstring.consolidation.policy import AdjudicationVerdict
    from redstring.domain.entity import Entity


@runtime_checkable
class CandidateSource(Protocol):
    """Supplies the entities that might be duplicates of a subject, scored."""

    async def candidates(
        self, subject: Entity, *, minimum_score: float = 0.0
    ) -> list[ScoredCandidate]:
        """Everything worth considering as a duplicate of `subject`, best first.

        Args:
            subject: The entity being consolidated around.
            minimum_score: Drop anything scoring below this. `resolve` passes
                its `low` threshold here, so a candidate under it is never
                built rather than being built and then discarded.

        Returns:
            Scored candidates in descending score. Ties must be broken by a
            further total order -- `CandidateFinder` uses ascending entity id
            as a string -- so that a cutoff falling inside a tie is decided
            the same way on every run.
        """
        ...


@runtime_checkable
class MergeAdjudicator(Protocol):
    """Settles the candidates whose score does not settle them."""

    async def adjudicate(
        self, subject: Entity, candidates: Sequence[ScoredCandidate]
    ) -> list[AdjudicationVerdict | None]:
        """One verdict per candidate, positionally aligned, `None` for no answer.

        Returning fewer verdicts than there are candidates is not permitted:
        the alignment between question and answer is the whole contract, and a
        short list silently records an answer about one pair against another.
        An implementation that cannot answer for some pairs returns `None` in
        those positions and keeps the length.
        """
        ...
