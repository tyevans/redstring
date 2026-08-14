"""Naming drift, counted directly rather than inferred from a total.

`extraction.mapping.entity_id_for` derives an entity's identity from its name,
so a chunk that says "Dudley" where another said "Dudley Dursley" does not
produce one entity with two mentions -- it produces two entities that no fold
can combine. That is the specific defect bounded concurrency risks, because a
wavefront gives a chunk less of what earlier chunks found.

**Entity count cannot see it.** Measured on one document: the entity-name sets
of a 3,000-character run and a 12,000-character run share a jaccard of 0.587,
while two repeats of one configuration share 0.601-0.667. A parameter that
moves everything else moves the total no more than noise does. Variant pairs
sat at 62 and 59 across the same comparison -- stable enough that a rise means
something.

**This is a floor on drift, not a count of it.** The heuristic sees
`dudley` / `dudley dursley`, where one name's tokens are a strict subset of
the other's. It cannot see `mum` / `mom`, or `dahl` / `roald dahl` where the
shorter is not a subset. Report it as a lower bound and never as a total.

**Expects lowercase input.** This function receives `normalized_name` from the
runner, which is already lowercased. Calling with mixed-case names would see
them as different tokens and give different results.

**The raw count is not comparable across runs with different entity counts.**
It is quadratic in the number of spellings of one entity and grows with the
total number of distinct names in a run, so two runs at different `chunk_size`
or `concurrency` will not in general extract the same entity count -- a rise
in `variant_pairs` is confounded with a rise in names unless the reader
divides by the entity count reported alongside it. `report.py` emits both in
the same JSON object for exactly this reason; read the ratio, not the bare
integer.
"""

from __future__ import annotations

from itertools import combinations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def _tokens(name: str) -> frozenset[str]:
    """Split a normalised name into comparable words.

    Possessives and hyphens are folded because they are spelling, not
    identity: `harry's wand` and `harry wand` are the same drift pair as
    `harry` and `harry potter`, and leaving them distinct would undercount.

    Note this strips any `'s`, not only a trailing possessive -- `o'sullivan`
    tokenises to `oullivan` rather than `osullivan`. Harmless for the count
    (both spellings of an O'-name mangle the same way, so a pair is still
    caught or missed consistently), but the docstring says "possessives" and
    the code does something slightly broader.
    """
    return frozenset(name.replace("'s", "").replace("'", "").replace("-", " ").split())


def _is_variant(a: str, b: str) -> bool:
    """True when one name is a strict token-subset of the other.

    Strict: a name is not a variant of itself, so a list containing the same
    name twice reports no drift.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return ta < tb or tb < ta


def variant_pairs_detail(names: Iterable[str]) -> list[tuple[str, str]]:
    """Every pair of names in one run that look like one entity spelled twice."""
    ordered = sorted(set(names))
    return [(a, b) for a, b in combinations(ordered, 2) if _is_variant(a, b)]


def variant_pairs(names: Iterable[str]) -> int:
    """How many such pairs there are.

    Pairs rather than clusters: three spellings of one name is worse than two,
    and a cluster count reports both as 1.
    """
    return len(variant_pairs_detail(names))
