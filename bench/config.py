"""The only YAML reader in the harness.

Everything downstream takes a `SweepPoint` or a `BenchConfig`, so a malformed
run is refused here -- before the first model call rather than after twenty
minutes of them. `scripts/mutation.py` takes the same posture for the same
reason: the expensive failure is the one that produces a plausible number.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path  # noqa: TC003
from typing import Any

import yaml


class BenchConfigError(Exception):
    """The config cannot produce a run worth making."""


@dataclass(frozen=True, slots=True)
class SweepPoint:
    """One timed run: a document at a chunk size and concurrency, once."""

    document_id: str
    chunk_size: int
    concurrency: int
    repeat: int


@dataclass(frozen=True, slots=True)
class BenchConfig:
    """A whole invocation's worth of knobs, resolved."""

    endpoint: str
    extraction_model: str
    embedding_model: str
    embedding_dimensions: int
    #: `LangChainLlmProvider`'s `max_tokens`, applied to every provider the
    #: harness builds. `tests/accuracy/test_extraction_accuracy.py` documents
    #: that the library default (`DEFAULT_MAX_TOKENS`, 8192) is too small for
    #: the graded corpus against a reasoning model -- it spends most of a
    #: short answer on chain of thought before `content` starts, surfacing as
    #: `EmptyCompletionError(finish_reason='length')`. That suite defaults to
    #: 16384; this is the same knob, in the file every other one lives in
    #: rather than hard-coded where a caller would not think to look.
    max_tokens: int
    graded: bool
    long_documents: tuple[str, ...]
    chunk_sizes: tuple[int, ...]
    concurrencies: tuple[int, ...]
    repeats: int
    stop_climbing_concurrency: bool
    per_document_timeout_s: float
    #: The parsed YAML exactly as written, embedded verbatim in the results
    #: file. A result that cannot say what produced it is an anecdote, and
    #: reconstructing the document from the fields above would silently drop
    #: any key a later version of the file adds.
    raw: dict[str, Any]

    def sweep(self) -> tuple[SweepPoint, ...]:
        """Every timed run, document-slowest and repeat-fastest.

        The order is part of the contract. Repeats of one configuration run
        together so a stability comparison is not separated by a re-chunk of a
        100k-character document, and the document varies slowest so the whole
        grid for one document is contiguous in the results file.
        """
        return tuple(
            SweepPoint(
                document_id=document, chunk_size=chunk_size, concurrency=concurrency, repeat=repeat
            )
            for document, chunk_size, concurrency, repeat in product(
                self.long_documents, self.chunk_sizes, self.concurrencies, range(self.repeats)
            )
        )


def _require(document: dict[str, Any], *path: str) -> Any:  # noqa: ANN401
    """Fetch a nested key, naming the full path when it is absent."""
    cursor: Any = document
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor:
            raise BenchConfigError(f"config is missing {'.'.join(path)}")
        cursor = cursor[key]
    return cursor


def load_config(path: Path) -> BenchConfig:
    """Read and validate a benchmark config.

    Raises:
        BenchConfigError: A required key is absent, or a value would produce a
            run that measures nothing.
    """
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise BenchConfigError(f"{path} is not a YAML mapping")

    concurrencies = tuple(_require(raw, "sweep", "concurrency"))
    if set(concurrencies) != {1}:
        raise BenchConfigError(
            f"concurrency {sorted(concurrencies)} needs deliverable C; the library "
            "extracts chunks serially, so any value but 1 would be ignored"
        )

    repeats = int(_require(raw, "policy", "repeats"))
    if repeats < 1:
        raise BenchConfigError(
            f"policy.repeats is {repeats}; a sweep of zero runs measures nothing"
        )

    long_documents = tuple(str(d) for d in _require(raw, "corpus", "long"))
    if not long_documents:
        raise BenchConfigError("corpus.long is empty; a sweep over no documents measures nothing")

    chunk_sizes = tuple(int(c) for c in _require(raw, "sweep", "chunk_size"))
    if not chunk_sizes:
        raise BenchConfigError(
            "sweep.chunk_size is empty; a sweep over no chunk sizes measures nothing"
        )

    return BenchConfig(
        endpoint=str(_require(raw, "endpoint")),
        extraction_model=str(_require(raw, "models", "extraction")),
        embedding_model=str(_require(raw, "models", "embedding")),
        embedding_dimensions=int(_require(raw, "models", "embedding_dimensions")),
        max_tokens=int(_require(raw, "models", "max_tokens")),
        graded=bool(_require(raw, "corpus", "graded")),
        long_documents=long_documents,
        chunk_sizes=chunk_sizes,
        concurrencies=tuple(int(c) for c in concurrencies),
        repeats=repeats,
        stop_climbing_concurrency=bool(_require(raw, "policy", "stop_climbing_concurrency")),
        per_document_timeout_s=float(_require(raw, "policy", "per_document_timeout_s")),
        raw=raw,
    )
