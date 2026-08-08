"""BM25: a term-weighted ranking, as arithmetic over supplied statistics.

This module knows nothing about chunks, stores or queries. It takes numbers
and returns a number, which is what makes the ranking identical across every
adapter that can supply the numbers.

## The score is unbounded and ordinal

BM25 is not on `0..1` and never was. A score is comparable to another score
**from the same query over the same corpus** and to nothing else: it moves
with corpus size, with document frequencies, and with how many terms the
query has. `domain/retrieval.py` makes the same statement about RRF and for
the same reason -- a number called "score" that a caller assumes is a
similarity is a bug that never raises.

## The IDF form is the one that cannot go negative

`ln(1 + (N - df + 0.5) / (df + 0.5))` is positive for every `0 <= df <= N`.
The unsmoothed Robertson/Sparck-Jones form goes negative once a term is in
more than half the corpus, which *penalises* a document for containing a
query term and reverses the ranking of two documents differing only in it.
The usual repair is a `max(0, ...)` floor, which discards the signal instead
of weighting it. Choosing a form that cannot go negative is better than
clamping one that can.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: Term-frequency saturation. Standard, and a module constant rather than a
#: parameter for the reason `RRF_K` is one: exposing it invites tuning against
#: a benchmark this repository does not have, and a value tuned on one
#: caller's corpus is the same arbitrary number with a better story.
BM25_K1 = 1.2

#: Length-normalisation strength. `0.0` disables normalisation entirely and
#: `1.0` applies it fully; `0.75` is the standard middle.
BM25_B = 0.75


class CorpusStats(BaseModel):
    """What a scorer needs to know about the corpus behind a candidate set.

    `doc_frequencies` covers the terms that were asked for. A term absent
    from it is treated as document frequency `0` -- the maximum weight --
    which is the correct reading for a term no document contains.
    """

    #: Chunks in this tenant's corpus. `0` means an empty corpus, and every
    #: score is then `0.0` rather than undefined.
    n_docs: int = Field(ge=0)
    #: Mean chunk length in tokens. `0.0` means every chunk is empty.
    avg_doc_length: float = Field(ge=0.0)
    doc_frequencies: dict[str, int] = Field(default_factory=dict)


def inverse_document_frequency(term: str, stats: CorpusStats) -> float:
    """How much a match on `term` is worth. See the module docstring."""
    if stats.n_docs == 0:
        return 0.0
    df = stats.doc_frequencies.get(term, 0)
    return math.log(1 + (stats.n_docs - df + 0.5) / (df + 0.5))


def bm25_score(
    terms: Sequence[str],
    term_frequencies: Mapping[str, int],
    doc_length: int,
    stats: CorpusStats,
) -> float:
    """Score one document against `terms`.

    Summed over **distinct terms in sorted order**. Distinct, because a query
    repeating a term does not make the document twice as relevant; sorted,
    because float addition is not associative and two adapters supplying the
    same terms in different orders must produce the *same* score -- the
    compliance suite compares their rankings for equality, not approximate
    equality.
    """
    if stats.n_docs == 0:
        return 0.0

    # An all-empty corpus has no length to normalise against. Every document
    # is then average, so the ratio is 0 and normalisation is the identity.
    ratio = 0.0 if stats.avg_doc_length == 0 else doc_length / stats.avg_doc_length

    total = 0.0
    for term in sorted(set(terms)):
        frequency = term_frequencies.get(term, 0)
        if frequency == 0:
            continue
        saturation = frequency * (BM25_K1 + 1)
        normalisation = frequency + BM25_K1 * (1 - BM25_B + BM25_B * ratio)
        total += inverse_document_frequency(term, stats) * saturation / normalisation
    return total
