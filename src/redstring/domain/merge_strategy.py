"""How a merge decides one property's value from several entities'.

Merging "Ada Lovelace" into "Augusta Ada King" leaves one entity and several
candidate values for every property. `resolve` is that choice, made per
property so a caller can keep the canonical description while unioning the
external ids.

## Two implemented, three refused loudly

`PREFER_CANONICAL` is the default: the entity the merge chose as canonical was
chosen for a reason, and its values win.

`UNION` is structural rather than a preference. Merging *inherently* produces
alias sets -- the whole point is that several names denote one thing -- so a
strategy that can accumulate values instead of picking one is not optional
equipment.

The other three raise `NotImplementedError` naming BACKLOG **B28**, and that
is the important part: **they do not fall back to the default.** A silent
fallback writes the canonical value while the caller believes it asked for a
deep merge, which corrupts data while looking like it worked, and leaves
nothing in the result to show it happened.

`LATEST` is not merely unimplemented, it is currently *unanswerable*.
Timestamps are per entity, not per property, so "the most recently updated
value" has no data behind it -- implementing it against the entity timestamp
would answer a different question in a way no caller could detect.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

_B28 = (
    "not implemented; see BACKLOG B28. Deliberately raising rather than "
    "falling back to PREFER_CANONICAL, which would write the canonical value "
    "while the caller believed it asked for something else"
)


class PropertyMergeStrategy(StrEnum):
    """How to reconcile one property across the entities a merge combines."""

    PREFER_CANONICAL = "prefer_canonical"
    UNION = "union"
    PREFER_MERGED = "prefer_merged"
    LATEST = "latest"
    DEEP_MERGE = "deep_merge"


#: The strategies that resolve. Everything else in the enum raises.
IMPLEMENTED = frozenset({PropertyMergeStrategy.PREFER_CANONICAL, PropertyMergeStrategy.UNION})


# ANN401 (`Any` in a signature) is silenced on the next two functions, and it
# is correct to silence it here rather than to narrow the type. The values are
# `Entity.properties` and `Entity.external_ids` entries, which are
# `dict[str, Any]` by declaration and hold whatever an extraction found -- a
# narrower annotation would be a claim this function cannot honour, and the
# alternative of a `TypeVar` would say only that the output type matches the
# input, which is false for `UNION` (it returns a list of them).
def resolve(
    strategy: PropertyMergeStrategy,
    *,
    canonical: Any,  # noqa: ANN401
    others: list[Any],
) -> Any:  # noqa: ANN401
    """The value `strategy` says one property should take after a merge.

    Args:
        strategy: Which rule to apply.
        canonical: The surviving entity's value. May be `None`, which is a
            value and not an absence -- `PREFER_CANONICAL` keeps it.
        others: The absorbed entities' values, in the order the merge listed
            those entities.

    Returns:
        For `PREFER_CANONICAL`, `canonical` unchanged. For `UNION`, a list
        holding every distinct value across `canonical` and `others`, canonical
        first, preserving first-seen order.

    Raises:
        NotImplementedError: For `PREFER_MERGED`, `LATEST` and `DEEP_MERGE`.
    """
    if strategy is PropertyMergeStrategy.PREFER_CANONICAL:
        return canonical
    if strategy is PropertyMergeStrategy.UNION:
        return _union(canonical, others)
    raise NotImplementedError(f"PropertyMergeStrategy.{strategy.name} is {_B28}")


def _union(canonical: Any, others: list[Any]) -> list[Any]:  # noqa: ANN401
    """Every distinct value, canonical first, in first-seen order.

    Deterministic order rather than a `set`, for two reasons that both bite:
    the values reach an event payload, where a set has no JSON form and no
    stable ordering to compare replays against; and they are frequently
    unhashable (a list of external ids, a dict of properties), so a `set`
    would raise on exactly the nested values `UNION` exists to accumulate.

    Comparison is therefore `==` over a list, which is O(n^2). The n here is
    the number of entities in one merge -- single digits -- and the
    alternative is either losing unhashable values or losing order.

    Flattens one level: a canonical value that is already a list of aliases
    unions element-wise with the others rather than nesting, so applying
    `UNION` twice does not produce `[[a, b], c]`. Idempotence matters because
    a projection replays.
    """
    merged: list[Any] = []
    for value in (canonical, *others):
        for element in value if isinstance(value, list) else [value]:
            if element not in merged:
                merged.append(element)
    return merged
