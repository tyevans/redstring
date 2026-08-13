"""Agreement between repeats of the same run. Never called accuracy.

Both sides of this comparison are produced by the code under test, so it
cannot distinguish a correct pipeline from a consistently incomplete one --
CLAUDE.md records the same shape letting three broken handlers pass a
replay-equivalence suite. What it can see is *variance*, which is the only
question it is asked: the risk in bounded concurrency (deliverable C) is
naming drift at chunk boundaries, and drift shows up here as instability.

Correctness is scored separately, against the graded corpus, by
`tests.accuracy`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class Stability:
    """How much two or more repeats agreed about which entities exist."""

    #: Intersection over union of the entity-name sets.
    jaccard: float
    #: Names every run found.
    always: int
    #: Names some run found and some run missed. The number to look at when
    #: `jaccard` drops: it counts the entities whose presence is a coin flip.
    sometimes: int
    runs: int


def stability_of(runs: Sequence[Sequence[str]]) -> Stability | None:
    """Compare the entity names of repeated runs.

    Returns `None` when there is nothing to compare -- fewer than two runs, or
    two runs that both extracted nothing. The empty case matters: 0/0 defined
    as 1.0 would report a dead endpoint as maximally stable, which is the
    failure this harness exists to refuse.
    """
    if len(runs) < 2:
        return None

    sets = [set(run) for run in runs]
    union = set[str]().union(*sets)
    if not union:
        return None

    intersection = set(sets[0]).intersection(*sets[1:])
    return Stability(
        jaccard=len(intersection) / len(union),
        always=len(intersection),
        sometimes=len(union) - len(intersection),
        runs=len(runs),
    )
