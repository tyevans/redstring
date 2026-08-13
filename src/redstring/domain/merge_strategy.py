"""How a merge decides one property's value from several entities'.

Merging "Ada Lovelace" into "Augusta Ada King" leaves one entity and several
candidate values for every property. `resolve` is that choice, made per
property so a caller can keep the canonical description while unioning the
external ids.

## Four implemented, one refused loudly

`PREFER_CANONICAL` is the default: the entity the merge chose as canonical was
chosen for a reason, and its values win.

`UNION` is structural rather than a preference. Merging *inherently* produces
alias sets -- the whole point is that several names denote one thing -- so a
strategy that can accumulate values instead of picking one is not optional
equipment.

`PREFER_MERGED` and `MOST_RECENTLY_OBSERVED` resolve because of the signature
below, and that is the whole story of this module's last change. `resolve` used
to take bare *values* -- `canonical=` and `others=`, with nothing about where
either came from. Every strategy needing more than the value itself was
unanswerable **by construction**, and no amount of new data on `Entity` would
have changed that while the call dropped it at the boundary. Taking
`PropertyClaim`s instead is the fix; `Provenance.observed_at` is a consequence
of it rather than the point. `PREFER_MERGED` was never even hard -- only
ill-defined about *which* absorbed entity when there are several, which the
ordered sequence settles.

`DEEP_MERGE` raises `NotImplementedError` naming BACKLOG **B28**, and that is
the important part: **it does not fall back to the default.** A silent fallback
writes the canonical value while the caller believes it asked for a deep merge,
which corrupts data while looking like it worked, and leaves nothing in the
result to show it happened. Nothing here makes a deep merge safer -- the
pre-merge shape is not recoverable from its result -- so it stays deferred on
its own merits, unrelated to the signature.

## `LATEST` became `MOST_RECENTLY_OBSERVED`, and that is a narrowing

The old name promised that the library knew when a property was last *updated*.
It does not and will not: nothing here tracks a property's edit history. What
is available is weaker and genuinely answerable -- of the entities asserting
this property, which was *observed* most recently. The name says the smaller
thing on purpose, because a caller reading `LATEST` would reasonably assume the
larger one and nothing in the result would contradict them.

The docstring this replaced argued `LATEST` was unanswerable because timestamps
were per entity rather than per property. That was wrong twice: there were no
per-entity timestamps either, and per-property timestamps were never the
obstacle. The signature was.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

# `EntityId` and `Provenance` are imported at runtime, not under
# `TYPE_CHECKING`: pydantic resolves `PropertyClaim`'s field annotations at
# schema-build time, and a type-checking-only import leaves the model "not
# fully defined" at every construction site. `Entity` and `datetime` appear
# only in plain function signatures and so stay below.
from redstring.domain.ids import EntityId
from redstring.domain.provenance import Provenance

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from redstring.domain.entity import Entity

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
    MOST_RECENTLY_OBSERVED = "most_recently_observed"
    DEEP_MERGE = "deep_merge"


#: The strategies that resolve. Everything else in the enum raises.
IMPLEMENTED = frozenset(
    {
        PropertyMergeStrategy.PREFER_CANONICAL,
        PropertyMergeStrategy.UNION,
        PropertyMergeStrategy.PREFER_MERGED,
        PropertyMergeStrategy.MOST_RECENTLY_OBSERVED,
    }
)


class PropertyClaim(BaseModel):
    """One entity's value for one property, with the observation behind it.

    `resolve` used to take bare values, and that -- not any missing timestamp --
    is why three of five strategies raised. A value alone cannot answer "which
    of these is most recent" or "how sure was whoever said this", so every
    strategy needing more than the value itself was unanswerable by
    construction. This type is the fix; the timestamp is a consequence of it.
    """

    value: Any
    provenance: Provenance
    origin: EntityId


def _order_key(claim: PropertyClaim) -> tuple[datetime, float, str]:
    """The total order `MOST_RECENTLY_OBSERVED` picks its winner under.

    Recency first, which is the strategy's whole content. Confidence second,
    because two observations made in the same batch share an instant *exactly*
    and preferring the surer one is the only meaningful thing left to say.

    `str(origin)` carries no meaning at all and is here solely so that no two
    distinct claims compare equal. The moment two do, `max` returns whichever
    arrived first and the winner depends on the order a caller happened to list
    the merged entities -- in a durable, replayable log. That is ADR 0010's
    argument, and it composes the same way `duplicate_preference` does: a
    meaningful order with an id appended.

    Deliberately *not* `domain.preference.preference`, which orders whole
    entities on `name`, `description` and `temporal` -- fields one property's
    claim does not have and cannot be asked about.
    """
    return (claim.provenance.observed_at, claim.provenance.confidence, str(claim.origin))


# ANN401 (`Any` in a signature) is silenced on the next two functions, and it
# is correct to silence it here rather than to narrow the type. The values are
# `Entity.properties` and `Entity.external_ids` entries, which are
# `dict[str, Any]` by declaration and hold whatever an extraction found -- a
# narrower annotation would be a claim this function cannot honour, and the
# alternative of a `TypeVar` would say only that the output type matches the
# input, which is false for `UNION` (it returns a list of them).
def resolve(strategy: PropertyMergeStrategy, claims: Sequence[PropertyClaim]) -> Any:  # noqa: ANN401
    """The value `strategy` says one property should take after a merge.

    Args:
        strategy: Which rule to apply.
        claims: Every claim about the property, canonical first, then the
            absorbed entities' in the order the merge listed them. A claim's
            value may be `None`, which is a value and not an absence --
            `PREFER_CANONICAL` keeps it. Positional rather than a
            `canonical=`/`others=` pair because every strategy but
            `PREFER_CANONICAL` treats them as one ordered sequence.

    Returns:
        For `PREFER_CANONICAL`, `claims[0].value`. For `PREFER_MERGED`,
        `claims[1].value`, or `claims[0].value` when nothing was absorbed. For
        `UNION`, a list holding every distinct value across the claims,
        canonical first, preserving first-seen order. For
        `MOST_RECENTLY_OBSERVED`, the value of the claim greatest under
        `_order_key`.

    Raises:
        ValueError: If `claims` is empty.
        NotImplementedError: For `DEEP_MERGE`.
    """
    if not claims:
        raise ValueError("resolve needs at least one claim; use claims_for, which may return none")
    if strategy is PropertyMergeStrategy.PREFER_CANONICAL:
        return claims[0].value
    if strategy is PropertyMergeStrategy.PREFER_MERGED:
        return claims[1].value if len(claims) > 1 else claims[0].value
    if strategy is PropertyMergeStrategy.UNION:
        return _union([c.value for c in claims])
    if strategy is PropertyMergeStrategy.MOST_RECENTLY_OBSERVED:
        return max(claims, key=_order_key).value
    raise NotImplementedError(f"PropertyMergeStrategy.{strategy.name} is {_B28}")


def _union(values: Sequence[Any]) -> list[Any]:
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
    for value in values:
        for element in value if isinstance(value, list) else [value]:
            if element not in merged:
                merged.append(element)
    return merged


def claims_for(
    property_name: str, canonical: Entity, others: Sequence[Entity]
) -> list[PropertyClaim]:
    """Every claim about `property_name`, canonical first.

    An entity whose `properties` lack the key is **skipped**, not given a
    `None` claim. Silence is not an assertion, and treating it as one would let
    an entity with no opinion outvote one with an opinion under
    `MOST_RECENTLY_OBSERVED` merely by being newer. An explicit `None` *is* a
    claim and is kept -- which is why this tests `in`, not truthiness.

    Returns `[]` when nobody claims the property, which the caller must
    distinguish from "everybody claimed `None`". `resolve` refuses an empty
    list rather than inventing an answer for it.
    """
    return [
        PropertyClaim(value=e.properties[property_name], provenance=e.provenance, origin=e.id)
        for e in (canonical, *others)
        if property_name in e.properties
    ]
