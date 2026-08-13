"""Combining two rankings into one, by rank and never by score.

## Why rank and not score

The semantic channel scores with cosine mapped onto `0..1`; the lexical
channel scores with Jaro-Winkler on `0..1`. The shared range is a coincidence
of both being normalized -- the two numbers have **no common unit**, and a
weighted sum of them silently invents an exchange rate that will be wrong for
some corpus and unfalsifiable for all of them. Reciprocal rank fusion uses
only the position, which is the one thing both channels genuinely produce.

The cost is real and worth stating: RRF discards magnitude, so a semantic
match at `0.99` and one at `0.51` contribute equally if both are ranked
first. That is why `ScoredEntity` retains the component scores -- the caller
can see what fusion threw away.

## Generic over the id type

This function is generic over any hashable id: `EntityId` (a `UUID` newtype)
and `ChunkId` (a hex-digest `str` newtype) both call it. The type parameter is
bound to `Hashable` rather than `str` -- the function's only real requirement
is that ids can be dict keys, and `Hashable` is that requirement stated
honestly. `EntityId` does *not* satisfy a `str` bound (it wraps a `UUID`, not
a `str`), so a `str` bound here would make the entity call site fail to type
check.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Hashable, Sequence

#: The `k` of `1/(k + rank)`, from Cormack, Clarke and Buettcher (SIGIR 2009),
#: "Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning
#: Methods", where 60 was found to work across runs without tuning.
#:
#: Deliberately a constant and not a parameter. Exposing it would invite
#: tuning against a benchmark this library does not have, and a value tuned on
#: one caller's corpus is not a better default -- it is the same arbitrary
#: number with a misleading provenance.
RRF_K = 60


def reciprocal_rank_fusion[IdT: Hashable](
    rankings: Sequence[Sequence[IdT]],
) -> list[tuple[IdT, float]]:
    """Fuse `rankings` into one, best first.

    Each ranking is best-first and may be empty; an empty one contributes
    nothing and does not shift any other ranking's positions, so turning a
    channel off cannot rescore the channel that stayed on.

    An id repeated within one ranking counts **once, at its best position** --
    a channel that emitted a duplicate must not be able to inflate its own
    contribution.

    Ties break by ascending `str(id).lower()`. For an `EntityId` this is the
    same canonical lowercase hyphenated string `VectorStore.search` already
    used to break ties; for a `ChunkId`, a hex digest, lowercasing is the
    identity. The type parameter is bound to `Hashable`, not `str`, because
    the function's real requirement is dict-key-ability -- `str(id).lower()`
    is what makes the order total across both id types without requiring
    either of them to *be* a `str`. The result is therefore a total order, so
    truncating to `k` through a tie cannot depend on dict ordering or on which
    channel ran first.
    """
    scores: defaultdict[IdT, float] = defaultdict(float)
    for ranking in rankings:
        seen: set[IdT] = set()
        for position, item_id in enumerate(ranking):
            if item_id in seen:
                continue
            seen.add(item_id)
            scores[item_id] += 1.0 / (RRF_K + position + 1)
    return sorted(scores.items(), key=lambda item: (-item[1], str(item[0]).lower()))
