"""The accuracy metric, tested without a model.

`tests/accuracy/scoring.py` is the half of B12 that needs no endpoint, and this
is why splitting it out was worth doing: a wrong metric is silent. A scorer
that counts an unresolvable edge as nothing, or defines empty precision as 0.0,
still prints three plausible numbers and nobody re-derives them by hand.

Every boundary the module names is pinned as its own case rather than left to a
property, per `.claude/rules/testing.md`: a sampler covering `0` depends on the
sampler, and these are the values where the definition is a *decision* rather
than arithmetic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from redstring import Entity, ExtractionMethod, Provenance, Relationship
from tests.accuracy.scoring import (
    ExpectedEntity,
    ExpectedRelationship,
    Score,
    score_entities,
    score_relationships,
)

TENANT = uuid4()

#: A fixed observation instant. Never `datetime.now(UTC)`: a fixture that
#: varies per run makes any comparison on `observed_at` non-deterministic.
OBSERVED = datetime(2026, 2, 11, 11, 7, tzinfo=UTC)


def entity(name: str, entity_type: str = "person") -> Entity:
    """An `Entity` with a fresh id, built directly rather than via a factory.

    `id`, `extraction_method` and `confidence` are required on `Entity` — it
    carries no defaults for them — so every field here is one the type demands,
    not one a helper is filling in on the test's behalf.

    The fresh `id` per call is load-bearing for the relationship tests: scoring
    resolves endpoints through it, and two entities sharing an id would let a
    reversed edge resolve to the right pair.
    """
    return Entity(
        id=uuid4(),
        tenant_id=TENANT,
        name=name,
        normalized_name=name.strip().lower(),
        entity_type=entity_type,
        provenance=Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.LLM,
            confidence=1.0,
            source_id="corpus",
        ),
    )


def relationship(source: Entity, target: Entity, relationship_type: str) -> Relationship:
    return Relationship(
        id=uuid4(),
        tenant_id=TENANT,
        source_entity_id=source.id,
        target_entity_id=target.id,
        relationship_type=relationship_type,
        confidence=1.0,
    )


class TestEntityMatching:
    def test_an_exact_match_is_a_true_positive(self):
        score = score_entities([ExpectedEntity("Ada Lovelace", "person")], [entity("Ada Lovelace")])

        assert (score.true_positives, score.false_positives, score.false_negatives) == (1, 0, 0)

    def test_matching_survives_case_and_whitespace(self):
        """The coupling to `normalize_name` is asserted here, on literals.

        Scoring calls the library's normalizer, so a broken normalizer would
        otherwise depress every accuracy number with no test to say why. This
        case fails first, and names the cause.
        """
        score = score_entities(
            [ExpectedEntity("Ada Lovelace", "person")], [entity("  ADA   lovelace ")]
        )

        assert score.true_positives == 1, "normalization is not being applied to names"

    def test_the_same_name_under_a_different_type_is_not_a_match(self):
        """Type is half the key. Without it, a `person` named `Geneva` would
        satisfy a graded `location` and the metric would flatter every run."""
        score = score_entities([ExpectedEntity("Geneva", "location")], [entity("Geneva", "person")])

        assert (score.true_positives, score.false_positives, score.false_negatives) == (0, 1, 1)

    def test_a_repeated_extraction_counts_once(self):
        """Sets, not multisets — `build_graph` merges across chunks, so a
        second mention is the pipeline working rather than a second claim."""
        score = score_entities(
            [ExpectedEntity("Ada Lovelace", "person")],
            [entity("Ada Lovelace"), entity("ada lovelace")],
        )

        assert (score.true_positives, score.false_positives) == (1, 0)

    def test_missing_and_spurious_are_counted_separately(self):
        score = score_entities(
            [ExpectedEntity("Ada", "person"), ExpectedEntity("Babbage", "person")],
            [entity("Ada"), entity("Turing")],
        )

        assert (score.true_positives, score.false_positives, score.false_negatives) == (1, 1, 1)


class TestRelationshipMatching:
    def test_endpoints_are_resolved_from_ids_back_to_names(self):
        """A graded document cannot know the ids a run will mint."""
        ada, babbage = entity("Ada"), entity("Babbage")
        score = score_relationships(
            [ExpectedRelationship("Ada", "Babbage", "worked_with")],
            [relationship(ada, babbage, "worked_with")],
            [ada, babbage],
        )

        assert score.true_positives == 1

    def test_direction_matters(self):
        """`worked_for` is not symmetric, and a scorer that sorted the endpoints
        would score a reversed edge as correct — which is precisely the bug an
        extraction suite exists to catch."""
        ada, babbage = entity("Ada"), entity("Babbage")
        score = score_relationships(
            [ExpectedRelationship("Ada", "Babbage", "worked_for")],
            [relationship(babbage, ada, "worked_for")],
            [ada, babbage],
        )

        assert (score.true_positives, score.false_positives, score.false_negatives) == (0, 1, 1)

    def test_an_edge_between_unextracted_entities_is_a_false_positive(self):
        """Not dropped. Dropping it lets a run inflate precision by emitting
        edges between entities it never extracted."""
        ada, babbage = entity("Ada"), entity("Babbage")
        score = score_relationships([], [relationship(ada, babbage, "worked_with")], [])

        assert score.false_positives == 1, "an unresolvable edge was silently discarded"
        assert score.precision == 0.0


class TestTheMetricDefinitions:
    """The boundaries where the definition is a decision, not arithmetic."""

    def test_predicting_nothing_is_perfect_precision_and_zero_recall(self):
        """A run that predicts nothing has made no wrong claims.

        Defining precision as 0.0 here would report an empty extraction as a
        *precision* failure and send the reader hunting for spurious entities
        that do not exist. Recall is the metric that should punish it.
        """
        score = Score(true_positives=0, false_positives=0, false_negatives=3)

        assert score.precision == 1.0
        assert score.recall == 0.0
        assert score.f1 == 0.0

    def test_expecting_nothing_is_perfect_recall(self):
        """The `empty-negative` corpus document depends on this.

        It grades no entities, so recall is vacuously 1.0 and precision is the
        only metric it can move — which is what makes it the one document that
        detects hallucination rather than rewarding recall.
        """
        score = Score(true_positives=0, false_positives=2, false_negatives=0)

        assert score.recall == 1.0
        assert score.precision == 0.0

    def test_a_perfect_empty_run_scores_one(self):
        score = Score(true_positives=0, false_positives=0, false_negatives=0)

        assert (score.precision, score.recall, score.f1) == (1.0, 1.0, 1.0)

    def test_f1_is_zero_rather_than_dividing_by_zero(self):
        """Reachable: one wrong prediction against one expected entity."""
        score = Score(true_positives=0, false_positives=1, false_negatives=1)

        assert (score.precision, score.recall) == (0.0, 0.0)
        assert score.f1 == 0.0

    @pytest.mark.parametrize(
        ("tp", "fp", "fn", "precision", "recall", "f1"),
        [
            (1, 1, 0, 0.5, 1.0, 2 / 3),
            (1, 0, 1, 1.0, 0.5, 2 / 3),
            (3, 1, 1, 0.75, 0.75, 0.75),
            (2, 2, 6, 0.5, 0.25, 1 / 3),
        ],
    )
    def test_the_arithmetic(
        self, tp: int, fp: int, fn: int, precision: float, recall: float, f1: float
    ):
        """Expectations written as literals, not as the formula under test.

        `2 * p * r / (p + r)` computed in the test would pass for any
        implementation of `f1` that used the same expression, including a wrong
        one — the failure shape CLAUDE.md records as writing the expectation in
        terms of the thing being checked. `2 / 3` and `0.75` are values, and
        the two fractions differ from each other in the way p and r differ.
        """
        score = Score(true_positives=tp, false_positives=fp, false_negatives=fn)

        assert score.precision == pytest.approx(precision)
        assert score.recall == pytest.approx(recall)
        assert score.f1 == pytest.approx(f1)

    def test_str_reports_the_counts_and_not_only_the_metrics(self):
        """An F1 of 0.5 does not say whether it is 1 of 2 or 500 of 1000, and a
        failure message that cannot say which is not actionable."""
        rendered = str(Score(true_positives=1, false_positives=1, false_negatives=1))

        assert "tp=1" in rendered
        assert "fp=1" in rendered
        assert "fn=1" in rendered
