"""Precision, recall and F1 for an extraction, as pure functions.

**This module never calls a model.** It compares what a run produced against
what a graded document says should have been produced, and that comparison is
ordinary deterministic code — so it lives in the commit gate
(`tests/unit/accuracy/test_scoring.py`) rather than behind a live endpoint.

Splitting it out this way is the reason the suite exists at all. B12 sat open
for eleven slices because "measure extraction accuracy" reads as one job that
needs a model, a corpus and a metric all at once. It is two jobs: deciding
whether a predicted entity *is* an expected one, which needs nothing, and
getting predictions, which needs everything. Only the second is expensive, and
only the first is where a wrong answer is silent.

## What counts as a match

An entity matches on `(normalized name, entity type)` and a relationship on
`(normalized source name, normalized target name, type)`. Names go through the
library's own `normalize_name`, deliberately: the question this suite asks is
whether extraction found the thing, and if the model returns `"Ada  Lovelace"`
where the corpus says `"Ada Lovelace"` that is a formatting difference the
library itself does not consider a difference.

That does couple the metric to `normalize_name`, so the scoring tests assert
the matching behaviour on literals (`"Ada Lovelace"` against
`"  ADA   lovelace "`) rather than trusting the function. A broken normalizer
fails those tests rather than quietly depressing every score.

**Relationships are compared by endpoint *name*, not by id.** A graded document
cannot know the `EntityId` a run will mint, so scoring resolves each
relationship's endpoints back through the entities the run produced. A
relationship whose endpoints are missing from that set cannot be resolved and
is counted as a false positive, which is the honest reading: an edge between
entities that were not extracted is not a correct edge.

## Sets, not multisets

Both sides are deduplicated by match key before counting. `build_graph` merges
across chunks, so a repeated entity is the pipeline working rather than a
second prediction, and counting it twice would penalise a longer document for
mentioning something twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from redstring.domain.normalization import normalize_name

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from uuid import UUID

    from redstring import Entity, Relationship


@dataclass(frozen=True, slots=True)
class ExpectedEntity:
    """One entity a graded document asserts is present in its text."""

    name: str
    entity_type: str


@dataclass(frozen=True, slots=True)
class ExpectedRelationship:
    """One relationship, stated by endpoint *name* because ids are per-run."""

    source: str
    target: str
    relationship_type: str


@dataclass(frozen=True, slots=True)
class Score:
    """Counts, and the three metrics derived from them.

    The counts are kept alongside the metrics rather than replaced by them: an
    F1 of 0.5 says nothing about whether the run found half of a large corpus
    or one of two entities, and the failure message needs to say which.
    """

    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float:
        """Of what was predicted, how much was right.

        **Defined as 1.0 when nothing was predicted**, rather than 0.0 or
        undefined. A run that predicts nothing has made no wrong claims, and
        the metric that should punish it is recall. Defining it as 0.0 would
        make an empty prediction look like a *precision* failure, which sends
        the reader looking for spurious entities that do not exist.
        """
        predicted = self.true_positives + self.false_positives
        return 1.0 if predicted == 0 else self.true_positives / predicted

    @property
    def recall(self) -> float:
        """Of what was expected, how much was found.

        1.0 when nothing was expected, by the same argument: a document graded
        with no entities cannot be under-extracted.
        """
        wanted = self.true_positives + self.false_negatives
        return 1.0 if wanted == 0 else self.true_positives / wanted

    @property
    def f1(self) -> float:
        """Harmonic mean, and 0.0 when both parts are 0.0.

        The zero guard is reachable: predicting one wrong entity for a document
        that expected one other entity gives precision 0.0 and recall 0.0.
        """
        p, r = self.precision, self.recall
        return 0.0 if p + r == 0.0 else 2 * p * r / (p + r)

    def __str__(self) -> str:
        return (
            f"P={self.precision:.3f} R={self.recall:.3f} F1={self.f1:.3f} "
            f"(tp={self.true_positives} fp={self.false_positives} "
            f"fn={self.false_negatives})"
        )


def _entity_key(name: str, entity_type: str) -> tuple[str, str]:
    return normalize_name(name), entity_type.strip().lower()


def score_entities(expected: Iterable[ExpectedEntity], actual: Iterable[Entity]) -> Score:
    """Compare extracted entities against a graded document's entities."""
    want = {_entity_key(e.name, e.entity_type) for e in expected}
    got = {_entity_key(e.name, e.entity_type) for e in actual}
    return Score(
        true_positives=len(want & got),
        false_positives=len(got - want),
        false_negatives=len(want - got),
    )


def score_relationships(
    expected: Iterable[ExpectedRelationship],
    actual: Iterable[Relationship],
    entities: Iterable[Entity],
) -> Score:
    """Compare extracted relationships, resolving endpoint ids back to names.

    An edge whose endpoints are not among `entities` is unresolvable and counts
    as a false positive rather than being dropped. Dropping it would let a run
    inflate precision by emitting edges between entities it never extracted.
    """
    by_id: Mapping[UUID, Entity] = {e.id: e for e in entities}

    want = {
        (normalize_name(r.source), normalize_name(r.target), r.relationship_type.strip().lower())
        for r in expected
    }

    got: set[tuple[str, str, str]] = set()
    unresolvable = 0
    for rel in actual:
        source = by_id.get(rel.source_entity_id)
        target = by_id.get(rel.target_entity_id)
        if source is None or target is None:
            unresolvable += 1
            continue
        got.add(
            (
                normalize_name(source.name),
                normalize_name(target.name),
                rel.relationship_type.strip().lower(),
            )
        )

    return Score(
        true_positives=len(want & got),
        false_positives=len(got - want) + unresolvable,
        false_negatives=len(want - got),
    )
