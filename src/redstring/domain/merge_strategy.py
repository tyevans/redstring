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

from collections.abc import Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, model_validator

# `EntityId` and `Provenance` are imported at runtime, not under
# `TYPE_CHECKING`: pydantic resolves `PropertyClaim`'s field annotations at
# schema-build time, and a type-checking-only import leaves the model "not
# fully defined" at every construction site. `Entity` and `datetime` appear
# only in plain function signatures and so stay below. `Mapping` is likewise
# a field annotation on `PropertyMergePolicy` and must resolve at
# schema-build time too.
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

#: The `Entity` fields a merge may decide. `name`, `entity_type` and `temporal`
#: are deliberately absent: preference between whole entities is ADR 0010's
#: `domain.preference`, and re-deciding `name` here would put two answers to
#: one question in the codebase.
MERGEABLE_FIELDS = frozenset({"description", "external_ids", "properties"})

#: The one field whose values `UNION` can legally produce. `external_ids` is
#: `dict[str, str]` and `description` is `str | None`; a list type-checks
#: against neither.
_UNION_FIELD = "properties"


class PropertyMergePolicy(BaseModel, frozen=True):
    """Which strategy applies to which field, by dotted path.

    One key space for a scalar field and a dict key:

    | Path | Means |
    |---|---|
    | `description` | the scalar field |
    | `properties` | the default for every key of `properties` |
    | `properties.role` | that one key |

    `strategy_for` resolves **exact path, then field default, then
    `default`**, and that order is the whole content of this type.

    Note the asymmetry with `claims_for` below: a bare `properties` is a
    legal *override key* here -- it names a per-field default -- while
    `claims_for` refuses a bare `properties` as a *claim path*, because a
    claim needs a key and resolving the whole dict as one value is not what
    any strategy means. Both rules are intentional; do not "fix" either to
    match the other.

    ## Two refusals, at two different times, on purpose

    `UNION` outside `properties` is refused **here, at construction**. It is a
    type error that nothing downstream can fix: the projection would hand a
    `list` to `Entity.external_ids`, pydantic would raise inside a fold, and
    the event is durably in the log by then with no way to make progress.
    Refusing when a caller wires up a service is the only point at which that
    is cheap.

    `DEEP_MERGE` is **not** refused here. It raises from `resolve` at plan
    time, on the write side, before any event exists -- so the failure is
    already cheap and already names BACKLOG B28. Encoding "which strategies
    are implemented" a second time in this validator would give that question
    two answers, and the one here would be the one nobody updates.
    """

    default: PropertyMergeStrategy = PropertyMergeStrategy.PREFER_CANONICAL
    overrides: Mapping[str, PropertyMergeStrategy] = {}

    @model_validator(mode="after")
    def _paths_are_real_and_union_stays_in_properties(self) -> PropertyMergePolicy:
        if self.default is PropertyMergeStrategy.UNION:
            raise ValueError(
                "UNION cannot be the policy default: it would reach description "
                "and external_ids, whose types a list does not satisfy"
            )
        for path, strategy in self.overrides.items():
            field = path.partition(".")[0]
            if field not in MERGEABLE_FIELDS:
                raise ValueError(
                    f"override path {path!r} names no mergeable field; "
                    f"expected one of {sorted(MERGEABLE_FIELDS)}"
                )
            if strategy is PropertyMergeStrategy.UNION and field != _UNION_FIELD:
                raise ValueError(
                    f"UNION is not legal on {path!r}: it returns a list, which "
                    f"{field} does not accept"
                )
        return self

    def strategy_for(self, path: str) -> PropertyMergeStrategy:
        """The strategy for `path`: exact, then its field's default, then `default`."""
        exact = self.overrides.get(path)
        if exact is not None:
            return exact
        field = self.overrides.get(path.partition(".")[0])
        if field is not None:
            return field
        return self.default


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
    claims *from distinct entities* compare equal -- which is every pair
    `claims_for` can produce, since it takes one claim per entity. That is the
    scope of the guarantee and the sentence is narrow on purpose: the key does
    not read `value`, so two claims constructed by hand with the same origin
    and different values do tie, and this order says nothing about which of
    those wins. The moment two claims do compare equal, `max` returns whichever
    arrived first and the winner depends on the order a caller happened to list
    the merged entities -- in a durable, replayable log. That is ADR 0010's
    argument, and it composes the same way `duplicate_preference` does: a
    meaningful order with an id appended.

    The totality property cannot exhibit the same-origin case and is not
    evidence about it: it draws `origin` from `st.uuids()`, which collides with
    probability nothing, so every generated pair differs in the third component
    before the first two are consulted. Widening it to a small `sampled_from`
    set of origins would reach the case -- and would then be asserting totality
    of an order that is *not* total over hand-built claims, so the property
    would have to change shape rather than merely its strategy. Left as is,
    with the claim narrowed to match, because `claims_for` is the only
    supported constructor and the narrower claim is the true one.

    Deliberately *not* `domain.preference.preference`, which orders whole
    entities on `name`, `description` and `temporal` -- fields one property's
    claim does not have and cannot be asked about.
    """
    return (claim.provenance.observed_at, claim.provenance.confidence, str(claim.origin))


# ANN401 (`Any` in a signature) is silenced on `resolve` below, and it
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


def claims_for(path: str, canonical: Entity, others: Sequence[Entity]) -> list[PropertyClaim]:
    """Every claim about `path`, canonical first.

    `path` is `description`, or `properties.<key>`, or `external_ids.<key>`.

    ## Silence is not an assertion, and the two field shapes say it differently

    An entity whose `properties` lack the key is **skipped**, not given a
    `None` claim -- treating absence as a claim would let an entity with no
    opinion outvote one with an opinion under `MOST_RECENTLY_OBSERVED` merely
    by being newer. An explicit `None` *is* a claim and is kept, which is why
    this tests `in`, not truthiness.

    `description` has no such distinction -- the field always exists and
    `None` is its absence, so **a `None` description is silence and is
    skipped**. The asymmetry is real rather than an inconsistency, and it is
    stated here because a reader who knows the dict rule will expect the
    opposite. (`PropertyMergePolicy` above has the mirror-image asymmetry: a
    bare `properties` is a legal *override key* there, naming a per-field
    default, while it is refused here as a claim path. Both are intentional.)

    A bare `properties` or `external_ids` is refused. Those are policy keys --
    a default for every key of the field -- and resolving a whole dict as one
    value is not what any strategy means.

    Returns `[]` when nobody claims the path, which the caller must
    distinguish from "everybody claimed `None`". `resolve` refuses an empty
    list rather than inventing an answer for it.
    """
    field, dot, key = path.partition(".")
    if field not in MERGEABLE_FIELDS:
        raise ValueError(
            f"{path!r} names no mergeable field; expected one of {sorted(MERGEABLE_FIELDS)}"
        )
    entities = (canonical, *others)
    if field == "description":
        if dot:
            raise ValueError(f"description is a scalar field; {path!r} names nothing")
        return [
            PropertyClaim(value=e.description, provenance=e.provenance, origin=e.id)
            for e in entities
            if e.description is not None
        ]
    if not dot:
        raise ValueError(
            f"{field!r} is a policy key, not a claim path; name a key, as in {field}.role"
        )
    return [
        PropertyClaim(value=getattr(e, field)[key], provenance=e.provenance, origin=e.id)
        for e in entities
        if key in getattr(e, field)
    ]
