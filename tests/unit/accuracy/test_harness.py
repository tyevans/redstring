"""Prove the accuracy harness measures, before believing any number it prints.

This is the direct analogue of `run-integration-and-mutation-suites.md`'s
"prove the harness works" step, and it exists for the same reason: **the two
ways an accuracy suite fails silently both produce a plausible number.** A
harness that extracts nothing reports F1 = 0.0 and reads as a bad model; one
that compares the expected set against itself reports 1.0 and reads as a good
one. Neither is about extraction, and neither raises.

So the harness is run here against `FakeLlmProvider`, whose answers are chosen,
with the score asserted exactly:

- a provider returning precisely the graded answer must score 1.0
- a provider returning nothing must score 0.0 recall — *and* 1.0 precision,
  which is the direction that catches a harness scoring against itself
- a provider returning something wrong must produce specific counts

The third is the one that would fail if the corpus were being compared to
itself: a self-comparison cannot produce a false positive no matter what the
model says.

`-m accuracy` collects nothing without a live model, so without this module the
whole suite would be unexercised by the commit gate — which is how it came to
be deleted the first time.
"""

from __future__ import annotations

import pytest

from redstring import FakeLlmProvider
from redstring.extraction.domains.registry import get_domain_schema
from tests.accuracy.corpus import GradedDocument, load_corpus
from tests.accuracy.runner import run_corpus, run_document
from tests.accuracy.scoring import ExpectedEntity, ExpectedRelationship

DOCUMENT = GradedDocument(
    id="harness-fixture",
    domain="news_journalism",
    text="Maria Chen leads Northwind Energy.",
    entities=(
        ExpectedEntity("Maria Chen", "person"),
        ExpectedEntity("Northwind Energy", "organization"),
    ),
    relationships=(ExpectedRelationship("Maria Chen", "Northwind Energy", "leads"),),
)

PERFECT = {
    "entities": [
        {"name": "Maria Chen", "entity_type": "person"},
        {"name": "Northwind Energy", "entity_type": "organization"},
    ],
    "relationships": [
        {
            "source_name": "Maria Chen",
            "target_name": "Northwind Energy",
            "relationship_type": "leads",
        }
    ],
}

NOTHING: dict[str, list[dict[str, str]]] = {"entities": [], "relationships": []}

WRONG = {
    "entities": [
        {"name": "Maria Chen", "entity_type": "person"},
        {"name": "Sundry Holdings", "entity_type": "organization"},
    ],
    "relationships": [],
}


def provider_returning(answer: object) -> FakeLlmProvider:
    """A provider that gives the same answer however many times it is asked.

    An empty `by_substring` with a `default`, rather than a `script`.
    `FakeLlmProvider` requires exactly one of the two, and a script has no
    misses — it must carry one entry per call, so it would couple these tests
    to how many chunks the chunker produces and to how many documents a test
    passes. The harness is what is under test here, not the chunker.
    """
    return FakeLlmProvider(by_substring={}, default=answer)


class TestTheHarnessMeasuresWhatItClaims:
    async def test_an_exactly_right_answer_scores_one(self):
        result = await run_document(DOCUMENT, provider=provider_returning(PERFECT))

        assert result.entities.f1 == 1.0, result.entities
        assert result.relationships.f1 == 1.0, result.relationships

    async def test_an_empty_answer_scores_zero_recall_and_full_precision(self):
        """The direction that catches a harness scoring the corpus against itself.

        A self-comparison would report 1.0 recall here, because the expected
        set always matches itself. Recall must be 0.0 — the model returned
        nothing — while precision stays 1.0, since returning nothing makes no
        wrong claim.
        """
        result = await run_document(DOCUMENT, provider=provider_returning(NOTHING))

        assert result.entities.recall == 0.0, result.entities
        assert result.entities.precision == 1.0, result.entities
        assert result.entities.true_positives == 0

    async def test_a_wrong_answer_produces_a_false_positive(self):
        """One right entity, one invented, one missed, and the edge not found.

        A harness comparing the corpus to itself cannot produce a false
        positive whatever the model says, so this assertion is the one that
        distinguishes a real measurement from a tautological one.
        """
        result = await run_document(DOCUMENT, provider=provider_returning(WRONG))

        assert result.entities.true_positives == 1
        assert result.entities.false_positives == 1, "the invented entity was not counted"
        assert result.entities.false_negatives == 1, "the missed entity was not counted"
        assert result.relationships.false_negatives == 1


class TestTheCorpusItself:
    def test_the_shipped_corpus_loads(self):
        corpus = load_corpus()

        assert len(corpus) >= 5
        assert {d.id for d in corpus} == {
            "newsroom-quote",
            "newsroom-event",
            "docs-function",
            "docs-class",
            "empty-negative",
        }

    def test_every_relationship_endpoint_is_a_graded_entity(self):
        """The loader enforces it; this proves the loader enforces it.

        An endpoint naming an ungraded entity can never be matched, so it would
        read as a model failure forever while being a grading mistake.
        """
        for document in load_corpus():
            graded = {e.name for e in document.entities}
            for rel in document.relationships:
                assert rel.source in graded, f"{document.id}: {rel.source}"
                assert rel.target in graded, f"{document.id}: {rel.target}"

    def test_a_corpus_with_an_ungraded_endpoint_is_rejected(self, tmp_path):
        """Proving the guard above can fail, rather than assuming it."""
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "documents:\n"
            "  - id: broken\n"
            "    domain: news_journalism\n"
            "    text: Someone did something.\n"
            "    entities:\n"
            "      - {name: Someone, type: person}\n"
            "    relationships:\n"
            "      - {source: Someone, target: Nobody, type: leads}\n"
        )

        with pytest.raises(ValueError, match="not a graded entity"):
            load_corpus(bad)

    def test_every_graded_type_is_declared_by_its_domain_schema(self):
        """Grading rule 2, which was prose until constrained decoding gave it
        teeth.

        A graded type no schema declares is *unreachable* for a run using
        `constrain_to_domain=True` -- the enum cannot produce it -- so it would
        score as a false negative no model could ever avoid, and the resulting
        "constrained extraction is worse" would be a property of the grading
        rather than of extraction. See `BACKLOG.md` B57.
        """
        for document in load_corpus():
            schema = get_domain_schema(document.domain)
            declared = {t.id for t in schema.entity_types}
            declared_edges = {t.id for t in schema.relationship_types}
            for entity in document.entities:
                assert entity.entity_type in declared, f"{document.id}: {entity.entity_type}"
            for rel in document.relationships:
                assert rel.relationship_type in declared_edges, (
                    f"{document.id}: {rel.relationship_type}"
                )

    def test_a_corpus_grading_an_undeclared_entity_type_is_rejected(self, tmp_path):
        """Proving that guard can fail. `company` is the obvious English word
        and `organization` is the schema's id, which is exactly the mistake a
        grader makes."""
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "documents:\n"
            "  - id: broken\n"
            "    domain: news_journalism\n"
            "    text: Northwind Energy did something.\n"
            "    entities:\n"
            "      - {name: Northwind Energy, type: company}\n"
        )

        with pytest.raises(ValueError, match="entity type"):
            load_corpus(bad)

    def test_a_corpus_grading_an_undeclared_relationship_type_is_rejected(self, tmp_path):
        """The other half of the same check.

        Enforcing only entity types would pass every test above, and
        relationship vocabularies are the *less* complete half of a domain
        schema -- so this is the side more likely to drift.
        """
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "documents:\n"
            "  - id: broken\n"
            "    domain: news_journalism\n"
            "    text: Maria Chen runs Northwind Energy.\n"
            "    entities:\n"
            "      - {name: Maria Chen, type: person}\n"
            "      - {name: Northwind Energy, type: organization}\n"
            "    relationships:\n"
            "      - {source: Maria Chen, target: Northwind Energy, type: runs}\n"
        )

        with pytest.raises(ValueError, match="relationship type"):
            load_corpus(bad)

    def test_an_empty_corpus_is_rejected_rather_than_scoring_perfectly(self, tmp_path):
        """A corpus of nothing scores 1.0 on everything, which is the silent
        success this whole module exists to make impossible."""
        empty = tmp_path / "empty.yaml"
        empty.write_text("documents: []\n")

        with pytest.raises(ValueError, match="no documents"):
            load_corpus(empty)

    def test_the_negative_document_grades_nothing(self):
        """`empty-negative` is the only document that can detect hallucination.

        Every other document rewards finding things, so a model that returns
        entities for everything scores well on all of them. This one grades no
        entities, making recall vacuous and precision the only movable metric.
        If it ever acquires a graded entity it stops doing that job.
        """
        negative = next(d for d in load_corpus() if d.id == "empty-negative")

        assert negative.entities == ()
        assert negative.relationships == ()


class TestTotalsAreSummedNotAveraged:
    async def test_a_short_document_does_not_get_equal_weight(self):
        """Totals sum counts rather than averaging per-document F1.

        Averaging would let adding a one-entity document move the headline
        number without anything about extraction changing — the metric would
        then be partly about corpus composition, which is not what it claims
        to report.
        """
        big = GradedDocument(
            id="big",
            domain="news_journalism",
            text="Maria Chen leads Northwind Energy.",
            entities=(
                ExpectedEntity("Maria Chen", "person"),
                ExpectedEntity("Northwind Energy", "organization"),
            ),
            relationships=(),
        )
        small = GradedDocument(
            id="small",
            domain="news_journalism",
            text="Maria Chen leads Northwind Energy.",
            entities=(ExpectedEntity("Nobody At All", "person"),),
            relationships=(),
        )

        result = await run_corpus([big, small], provider=provider_returning(PERFECT))

        # big: 2 tp. small: 0 tp, 2 fp (the two real entities), 1 fn.
        assert result.entities.true_positives == 2
        assert result.entities.false_negatives == 1
        # Averaging the two documents' recall would give (1.0 + 0.0) / 2 = 0.5.
        # Summing gives 2 / 3.
        assert result.entities.recall == pytest.approx(2 / 3)
