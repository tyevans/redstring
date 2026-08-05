"""The graded corpus: documents with the entities and edges they contain.

Loading is separate from scoring for the same reason scoring is separate from
the model call — each of the three fails differently, and a suite that cannot
say which one failed is not a measurement.

**This is a starter corpus, not a benchmark, and the difference matters when
reading a number off it.** Five short documents hand-graded by one person
measure whether extraction is working; they do not measure how well it works
relative to anything, and a change in F1 across such a small set is noise until
it is large. `corpus.yaml` says so at the top and the assertion floors in
`test_extraction_accuracy.py` are set accordingly — low enough that only a real
regression trips them.

The grading convention is the part to preserve if the corpus grows:

- **Grade what the text states, not what is true.** "Ada Lovelace worked with
  Charles Babbage" grades a `worked_with` edge. That she was a mathematician is
  true and ungraded, because the document does not say it, and an extractor
  that supplied it from its own knowledge would be *wrong* here even though the
  fact is right. This is the one rule a second grader will get wrong.
- **Entity types come from the schema being used**, so a corpus document names
  the domain it should be extracted under.
- **Omission is a claim.** Anything not listed is scored as a false positive if
  extracted, so a partially graded document reports a precision failure that
  belongs to the grader rather than to the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from tests.accuracy.scoring import ExpectedEntity, ExpectedRelationship

CORPUS_PATH = Path(__file__).parent / "corpus.yaml"


@dataclass(frozen=True, slots=True)
class GradedDocument:
    """One document, its domain, and everything its text is asserted to state."""

    id: str
    domain: str
    text: str
    entities: tuple[ExpectedEntity, ...]
    relationships: tuple[ExpectedRelationship, ...]


def load_corpus(path: Path | None = None) -> tuple[GradedDocument, ...]:
    """Read and validate the corpus.

    Raises rather than skipping on a malformed file: a corpus that fails to
    load is a broken test asset, not an absent backend, and the two must not
    look alike from the outside. The whole point of this suite is that a silent
    zero is indistinguishable from a silent everything.
    """
    raw = yaml.safe_load((path or CORPUS_PATH).read_text())
    documents = raw["documents"]
    if not documents:
        raise ValueError(f"{path or CORPUS_PATH} contains no documents")

    loaded = []
    seen: set[str] = set()
    for entry in documents:
        doc_id = entry["id"]
        if doc_id in seen:
            raise ValueError(f"duplicate corpus document id: {doc_id!r}")
        seen.add(doc_id)

        entities = tuple(
            ExpectedEntity(name=e["name"], entity_type=e["type"]) for e in entry.get("entities", ())
        )
        relationships = tuple(
            ExpectedRelationship(
                source=r["source"], target=r["target"], relationship_type=r["type"]
            )
            for r in entry.get("relationships", ())
        )

        # An edge naming an entity the document does not grade would be scored
        # against a name that can never be matched, which reads as a model
        # failure and is a grading error. Caught here, where it is cheap.
        graded_names = {e.name for e in entities}
        for rel in relationships:
            for endpoint in (rel.source, rel.target):
                if endpoint not in graded_names:
                    raise ValueError(
                        f"{doc_id}: relationship endpoint {endpoint!r} is not a "
                        f"graded entity of that document"
                    )

        loaded.append(
            GradedDocument(
                id=doc_id,
                domain=entry["domain"],
                text=entry["text"],
                entities=entities,
                relationships=relationships,
            )
        )
    return tuple(loaded)
