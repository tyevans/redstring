"""A benchmark document is third-party text living in the repository. The
metadata beside it is what makes that a decision rather than an accident."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from bench.corpus import BenchCorpusError, load_document

if TYPE_CHECKING:
    from pathlib import Path

META = """
source: https://en.wikipedia.org/wiki/Harry_Potter_and_the_Philosopher's_Stone
retrieved: 2026-08-13
licence: CC BY-SA 4.0
"""


def seed(root: Path, document_id: str = "hp1", *, text: str = "Harry met Hagrid.") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{document_id}.txt").write_text(text)
    (root / f"{document_id}.meta.yaml").write_text(META)
    return root


def test_the_text_and_its_provenance_load_together(tmp_path: Path) -> None:
    document = load_document("hp1", root=seed(tmp_path))

    assert document.id == "hp1"
    assert document.text == "Harry met Hagrid."
    assert document.retrieved == "2026-08-13"
    assert document.licence == "CC BY-SA 4.0"
    assert document.source.startswith("https://en.wikipedia.org/")


def test_text_without_metadata_is_refused(tmp_path: Path) -> None:
    """Committed third-party text whose origin nobody recorded."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "orphan.txt").write_text("some text")

    with pytest.raises(BenchCorpusError, match=re.escape("orphan.meta.yaml")):
        load_document("orphan", root=tmp_path)


def test_metadata_missing_a_field_names_the_field(tmp_path: Path) -> None:
    root = seed(tmp_path)
    (root / "hp1.meta.yaml").write_text("source: https://example.com\nretrieved: 2026-08-13\n")

    with pytest.raises(BenchCorpusError, match="licence"):
        load_document("hp1", root=root)


def test_an_absent_document_names_the_path_it_looked_for(tmp_path: Path) -> None:
    with pytest.raises(BenchCorpusError, match=re.escape("missing.txt")):
        load_document("missing", root=tmp_path)


def test_an_empty_document_is_refused(tmp_path: Path) -> None:
    """An empty document extracts nothing in no time -- the fastest run in
    any grid, and a benchmark's version of a zero-survivor mutation run."""
    with pytest.raises(BenchCorpusError, match="empty"):
        load_document("hp1", root=seed(tmp_path, text="   \n  "))
