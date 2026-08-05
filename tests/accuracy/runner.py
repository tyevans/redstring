"""Run the graded corpus through extraction and score the result.

Takes an `LlmProvider` rather than reaching for one. That is what lets the same
code path be exercised twice: once in the commit gate against
`FakeLlmProvider`, where the right answer is known exactly and the harness can
be *proved* to measure it, and once behind `-m accuracy` against a live model,
where the answer is what is being measured.

**The first of those is not a formality.** This repository's standing lesson
about mutation runs — a zero-survivor result usually means the harness never
ran — applies identically here: an accuracy suite that silently extracts
nothing reports F1 = 0.0 and looks like a bad model, and one that silently
scores the expected set against itself reports 1.0 and looks like a good one.
Neither number is about extraction. `tests/unit/accuracy/test_harness.py`
pins both directions against a scripted provider before any live number is
believed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from redstring import InMemoryGraphStore, SourceDocument, build_graph
from tests.accuracy.scoring import Score, score_entities, score_relationships

if TYPE_CHECKING:
    from collections.abc import Iterable

    from redstring import LlmProvider
    from tests.accuracy.corpus import GradedDocument


@dataclass(frozen=True, slots=True)
class DocumentResult:
    """What one graded document scored, and enough context to read it."""

    document_id: str
    entities: Score
    relationships: Score

    def __str__(self) -> str:
        return (
            f"{self.document_id}\n"
            f"    entities:      {self.entities}\n"
            f"    relationships: {self.relationships}"
        )


@dataclass(frozen=True, slots=True)
class CorpusResult:
    """Per-document results plus the corpus totals.

    Totals are computed by **summing the counts**, not by averaging the
    per-document F1s. Averaging weights a one-entity document the same as a
    twenty-entity one, so adding a short document would move the headline
    number without anything about extraction changing.
    """

    documents: tuple[DocumentResult, ...]

    @property
    def entities(self) -> Score:
        return _total(r.entities for r in self.documents)

    @property
    def relationships(self) -> Score:
        return _total(r.relationships for r in self.documents)

    def report(self) -> str:
        lines = [str(r) for r in self.documents]
        lines.append(f"  TOTAL entities:      {self.entities}")
        lines.append(f"  TOTAL relationships: {self.relationships}")
        return "\n".join(lines)


def _total(scores: Iterable[Score]) -> Score:
    scores = list(scores)
    return Score(
        true_positives=sum(s.true_positives for s in scores),
        false_positives=sum(s.false_positives for s in scores),
        false_negatives=sum(s.false_negatives for s in scores),
    )


async def run_document(document: GradedDocument, *, provider: LlmProvider) -> DocumentResult:
    """Extract one graded document into a fresh store and score what lands.

    A fresh tenant and a fresh store per document, so nothing a previous
    document extracted can be counted for this one — the accuracy analogue of
    the compliance suites' `new_store()` per example.
    """
    tenant_id = uuid4()
    store = InMemoryGraphStore()

    await build_graph(
        SourceDocument(id=document.id, text=document.text),
        provider=provider,
        store=store,
        tenant_id=tenant_id,
        domain=document.domain,
    )

    entities = await store.find_entities(tenant_id)
    # One call taking every id, not a call per entity: the port takes a
    # sequence. An edge still comes back once per endpoint it matches, so
    # deduplicate by id before scoring or every internal edge counts twice.
    relationships = await store.get_relationships_for([e.id for e in entities], tenant_id)
    unique = {rel.id: rel for rel in relationships}.values()

    return DocumentResult(
        document_id=document.id,
        entities=score_entities(document.entities, entities),
        relationships=score_relationships(document.relationships, unique, entities),
    )


async def run_corpus(documents: Iterable[GradedDocument], *, provider: LlmProvider) -> CorpusResult:
    return CorpusResult(
        documents=tuple([await run_document(d, provider=provider) for d in documents])
    )
