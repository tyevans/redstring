"""Temporal relations between entities, **computed on read**.

An inferred edge is a pure function of two extents. That single sentence
decides everything below.

## Why nothing here is persisted, and why that was not the obvious choice

The alternative was to emit inferred edges as `Relationship`s inside
`DocumentExtracted`, alongside the ones the model stated. It has one real
advantage -- the graph store would answer "what preceded this" directly, with
no fan-out -- and three problems that outweigh it:

1. **It duplicates state that can disagree with its inputs.** Re-extraction
   under a new model version is the supported way an entity's dates improve,
   and improving one extent invalidates every inferred edge touching it. There
   is no `TemporalRelationInvalidated` event and there will not be one: ADR
   0001's granularity is deliberately coarse. So the stored edges would go
   stale silently, and a stale `PRECEDES` looks exactly like a fresh one.
2. **It is quadratic in the tenant, not in the document.** The edges worth
   having are mostly *between* documents -- an event in one document preceding
   an event in another -- and extraction only ever sees one document. Emitting
   them from extraction would produce the within-document subset, which is the
   least interesting part, while looking like the whole answer.
3. **It puts a derived fact in the durable log.** The log is what the system
   knows; this is arithmetic over what it knows. Storing it means a replay can
   produce edges that disagree with the same arithmetic run today, which is the
   defect the whole re-architecture exists to remove.

The cost is honest and stated: computing on read is O(n^2) in dated entities,
`max_pairs` bounds it, and B48 records the query cost that goes with it.

## One edge per pair, always the same way round

`relate` is symmetric -- `a BEFORE b` and `b AFTER a` are one fact. Emitting
both would double the output and make "how many relations are there" depend on
input order. So each pair is reduced to one edge, and `AFTER` and `DURING`
never appear in the output: an `AFTER` is emitted as its target's `BEFORE`, a
`DURING` as its target's `CONTAINS`.

**That reduction happens after the comparison, not before, and the distinction
is the whole of it.** The first version canonicalised first -- sort the pair by
interval, call the earlier one the source -- and the invariant above was then
an *argument* about sort order rather than a property of the code. The argument
was wrong. `order_key` sorts by lower bound and then by upper bound ascending,
so two intervals sharing a lower bound put the **shorter** one first, and
`relate` from shorter to longer is `DURING` -- which `INFERRED_RELATIONS` then
discarded, losing the edge entirely.

The pair that broke it is not exotic: "2023" and "2023-2025" both come straight
out of the parser, as do a month and the year it opens, and an event and the
era that begins with it. Every one of those had no edge at all.

Inverting after the fact makes the invariant true by construction: whatever
direction the pair arrives in, `_CANONICAL` decides. `order_key` still orders
the pairs, but now only so that `OVERLAPS` and `EQUALS` -- which are genuinely
symmetric and have no earlier side -- get a *deterministic* direction rather
than a correct one. Nothing else in this module depends on it for correctness.

## `InferredRelation` is not a `Relationship`, deliberately

It has no `id`, so it cannot be handed to `GraphStore.upsert_relationship`, and
`isinstance` distinguishes it from anything the log recorded. Given point 1
above, the ability to persist one of these by accident is the specific mistake
worth making impossible rather than merely discouraged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, NamedTuple

from redstring.domain.interval import TemporalRelation, bounds, relate_bounds

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence

    from redstring.domain.entity import Entity
    from redstring.domain.ids import EntityId
    from redstring.domain.interval import Bounds
    from redstring.domain.temporal import TemporalExtent

#: How many pairs `infer_relations` will compare before refusing. Roughly a
#: thousand dated entities. Chosen as a number that a tenant reaches by
#: accident rather than one anybody asks for on purpose: past it, the honest
#: answer is that this shape of query needs the port method B48 describes, not
#: that it needs more patience.
DEFAULT_MAX_PAIRS: Final = 500_000

#: The inverse of each relation, used to reduce a pair to one edge. `BEFORE`
#: and `CONTAINS` are the directions kept; `AFTER` and `DURING` are emitted as
#: their opposites with the endpoints swapped. `OVERLAPS` and `EQUALS` are
#: their own inverses, so for those the pair's order is settled by `order_key`
#: instead -- deterministic rather than meaningful, because neither has an
#: earlier side.
_CANONICAL: Final = {
    TemporalRelation.AFTER: TemporalRelation.BEFORE,
    TemporalRelation.DURING: TemporalRelation.CONTAINS,
}

#: What a caller gets when it does not filter. `AFTER` and `DURING` are absent
#: because `_CANONICAL` guarantees they are never produced. That guarantee is
#: now structural; it used to be an argument about sort order, and the argument
#: was wrong -- see the module docstring.
INFERRED_RELATIONS: Final = frozenset(
    {
        TemporalRelation.BEFORE,
        TemporalRelation.CONTAINS,
        TemporalRelation.OVERLAPS,
        TemporalRelation.EQUALS,
    }
)


class InferredRelation(NamedTuple):
    """How one entity stands to another in time. Derived, never stored.

    A tuple so that a result set sorts without a key function, which is what
    makes "the same graph gives the same answer" checkable rather than merely
    intended. Not *hashable*, because `TemporalExtent` is a pydantic model and
    is not -- so a caller wanting a set of these should key on the endpoint
    ids. Comparison is what the ordering needs; hashing was never used.
    """

    source_entity_id: EntityId
    target_entity_id: EntityId
    relation: TemporalRelation
    source_name: str
    target_name: str
    #: The extents this was computed from, so a caller can show its working.
    #: An inferred edge with no visible derivation is indistinguishable from
    #: an asserted one, which is the confusion this module exists to avoid.
    source_extent: TemporalExtent | None = None
    target_extent: TemporalExtent | None = None


def infer_relations(
    entities: Iterable[Entity],
    *,
    relations: Collection[TemporalRelation] = INFERRED_RELATIONS,
    max_pairs: int = DEFAULT_MAX_PAIRS,
) -> list[InferredRelation]:
    """Every temporal relation holding between the dated members of `entities`.

    Args:
        entities: Any collection. Undated members -- no extent, or an extent
            holding only a `sequence_position` -- take no part rather than
            being an error: most entities in a graph are not events.
        relations: Which relations to return. Defaults to all four that
            canonical ordering can produce.
        max_pairs: Refuse rather than grind. See `DEFAULT_MAX_PAIRS`.

    Returns:
        One `InferredRelation` per related pair, sorted, with no duplicates
        and no inverses. Deterministic: the same entities in any order give
        the same list.

    Raises:
        ValueError: More pairs than `max_pairs`. Raised *before* any
            comparison, so the refusal costs nothing and cannot itself be the
            slow thing it is preventing.
    """
    dated = [
        (entity, interval)
        for entity, interval in (
            (e, bounds(e.temporal) if e.temporal is not None else None) for e in _unique(entities)
        )
        if interval is not None
    ]
    pairs = len(dated) * (len(dated) - 1) // 2
    if pairs > max_pairs:
        raise ValueError(
            f"{len(dated)} dated entities is {pairs} pairs, over max_pairs={max_pairs}. "
            f"Inference is quadratic and computed on read; narrow the entity set, or "
            f"raise the cap knowingly."
        )

    # Sorted so that `OVERLAPS` and `EQUALS` -- the two relations with no
    # earlier side -- get a stable direction. It does **not** decide direction
    # for the asymmetric relations; `_CANONICAL` does, after the comparison.
    dated.sort(key=lambda pair: order_key(pair[1], pair[0].id))

    wanted = frozenset(relations)
    found: list[InferredRelation] = []
    for index, (first, first_bounds) in enumerate(dated):
        for second, second_bounds in dated[index + 1 :]:
            relation = relate_bounds(first_bounds, second_bounds)
            inverse = _CANONICAL.get(relation)
            source, target = (second, first) if inverse is not None else (first, second)
            relation = inverse if inverse is not None else relation
            if relation in wanted:
                found.append(
                    InferredRelation(
                        source_entity_id=source.id,
                        target_entity_id=target.id,
                        relation=relation,
                        source_name=source.name,
                        target_name=target.name,
                        source_extent=source.temporal,
                        target_extent=target.temporal,
                    )
                )
    return sorted(found)


def order_key(interval: Bounds, entity_id: EntityId) -> tuple[int, str, int, str, str]:
    """A total, sortable key for an interval and the entity holding it.

    Public because `temporal.query.timeline` orders by the same key, and two
    definitions of "chronological" that disagree would put the timeline in one
    order and the relations derived from it in another.

    `Bounds` cannot be sorted directly: it holds `datetime | None`, and
    comparing `None` with a `datetime` raises. Each end becomes a rank and a
    value, where the rank alone separates the infinities -- minus infinity
    below every finite lower bound, plus infinity above every finite upper one
    -- and the value is consulted only when the ranks agree.

    The rank is why there is no sentinel datetime. `datetime.min` standing in
    for minus infinity would collide with a genuine date in year 1, which this
    parser produces from "1st century", and the collision would sort an
    open-ended interval as merely very old.

    `isoformat()` rather than the `datetime` itself so that mypy sees one type
    per tuple slot; the ISO rendering is fixed-width within an era and sorts
    identically to the value it came from.

    The entity id is the final component and makes the key **total**: two
    entities can carry the same extent, `EQUALS` has no earlier side to prefer,
    and without a tie-break the pair's direction would depend on input order.

    **A known-equivalent mutant lives on the lower rank.** Changing its `0` to
    `1` changes nothing, because `""` sorts below every ISO rendering, so an
    open lower bound comes first either way. The rank is kept for symmetry with
    the upper one, where it is load-bearing: there `2` is doing real work, since
    `""` would otherwise sort an unbounded end *first*. Verified by mutating
    each: the upper one fails a test, the lower one does not.
    """
    lower_rank, lower = (0, "") if interval.lower is None else (1, interval.lower.isoformat())
    upper_rank, upper = (2, "") if interval.upper is None else (1, interval.upper.isoformat())
    return (lower_rank, lower, upper_rank, upper, str(entity_id))


def _unique(entities: Iterable[Entity]) -> Sequence[Entity]:
    """`entities` with repeats of one id removed, first occurrence kept.

    A caller assembling a set from several reads can easily hand the same
    entity twice, and a self-pair compares an interval with itself and reports
    `EQUALS` -- which reads as a genuine finding about two entities.
    """
    seen: dict[EntityId, Entity] = {}
    for entity in entities:
        seen.setdefault(entity.id, entity)
    return list(seen.values())
