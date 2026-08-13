"""The long documents, and the provenance that makes committing them a decision.

redstring never fetches, and the benchmark does not either: the operator puts
the text in `bench/corpus/` and records where it came from beside it. A
document without a `.meta.yaml` is refused rather than defaulted, because a
default here is third-party text in a repository with nobody's name on the
decision to put it there.

These documents are **ungraded**, so nothing scored against them is accuracy.
See BACKLOG B-BENCH-1 for why grading a 100k-character document was deferred
rather than skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

CORPUS_ROOT: Final[Path] = Path(__file__).parent / "corpus"

_REQUIRED_META: Final[tuple[str, ...]] = ("source", "retrieved", "licence")


class BenchCorpusError(Exception):
    """A benchmark document is absent, empty, or unattributed."""


@dataclass(frozen=True, slots=True)
class BenchDocument:
    """One long document and where it came from."""

    id: str
    text: str
    source: str
    retrieved: str
    licence: str


def load_document(document_id: str, *, root: Path = CORPUS_ROOT) -> BenchDocument:
    """Load a benchmark document and its provenance.

    Raises:
        BenchCorpusError: The text is missing, blank, or has no metadata
            beside it naming source, retrieval date and licence.
    """
    text_path = root / f"{document_id}.txt"
    meta_path = root / f"{document_id}.meta.yaml"

    if not text_path.is_file():
        raise BenchCorpusError(f"no benchmark document at {text_path}")
    if not meta_path.is_file():
        raise BenchCorpusError(
            f"{text_path.name} has no provenance; write {meta_path.name} naming "
            f"{', '.join(_REQUIRED_META)}"
        )

    text = text_path.read_text()
    if not text.strip():
        raise BenchCorpusError(
            f"{text_path} is empty; an empty document is the fastest run there is"
        )

    meta = yaml.safe_load(meta_path.read_text()) or {}
    missing = [key for key in _REQUIRED_META if not meta.get(key)]
    if missing:
        raise BenchCorpusError(f"{meta_path.name} is missing {', '.join(missing)}")

    return BenchDocument(
        id=document_id,
        text=text,
        source=str(meta["source"]),
        retrieved=str(meta["retrieved"]),
        licence=str(meta["licence"]),
    )
