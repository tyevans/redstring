"""What a merge does to the edge set, computed before anything is emitted.

Absorbing `B` into `A` moves every edge touching `B` onto `A`. Three things can
happen to an edge, and `plan_redirections` decides which:

| Edge | Becomes |
|---|---|
| `B -> X` | `A -> X`, a `RelationshipRedirection` with both sides |
| `B -> C`, both absorbed | dropped -- it would be a self-loop on `A` |
| `B -> X` duplicating an `A -> X` of the same type | one of the two is dropped |

The result is the *whole* effect on the edge set, which is why `EntitiesMerged`
carries it: recomputing it later needs the pre-merge graph, and the projection
overwrote that when it applied the event.

## Why dropping is a redirection with `after=None` rather than an omission

An omitted edge is indistinguishable from an edge the merge never saw, and undo
would have nothing to recreate it from. `RelationshipRedirection` keeps the
whole `before` `Relationship` -- not a pair of endpoint ids -- because a
recreated edge needs its type, confidence and properties back too.

## Deduplication, and why it is safe

`GraphStore.delete_relationship`'s docstring left this decision open. It is
taken here: **merge deduplicates parallel edges.** Redirecting `B -> X` onto
`A` when `A -> X` of the same type already exists would leave two edges saying
the same thing, differing only in an id nobody chose.

It is safe precisely because dropping is recorded rather than silent -- undo
recreates the edge from `before`, so merge-then-undo still reproduces the
pre-merge graph exactly. Without the `after=None` representation this would be
a lossy optimisation and would not be worth doing.

## Which duplicate survives is decided by a total order, not by arrival

`GraphStore.get_relationships_for` promises **no** order. So "keep the first
one seen" would make the surviving edge id depend on how an adapter happened to
sort -- two backends would produce different `EntitiesMerged` payloads for the
same graph, and the same backend might differ between runs.

**Order-independent is not the same as instant-independent.** Everything below
says the plan is a function of the graph; it is a function of the graph *at the
moment the caller read it*, and an edge created after that read is not
deduplicated by anything, ever. See BACKLOG B43 and
`tests/unit/consolidation/test_known_gaps.py`.

`duplicate_preference` is the order, and it *composes* rather than redefines:
`redstring.domain.preference.relationship_preference` decides on confidence
and properties, exactly as extraction's two deduplications do, and this module
appends the id.

**The totality argument, because a `>` mutated to `>=` is only equivalent when
the order really is total.** Every edge competing for one signature agrees on
`source_entity_id`, `target_entity_id` and `relationship_type` -- that *is* the
signature -- and on `tenant_id`, since `get_relationships_for` is tenant-scoped
and `RelationshipRedirection` refuses a cross-tenant move. `Relationship` has
exactly seven fields, so the only ones left to vary are `confidence`,
`properties` and `id`. The first two are what `relationship_preference` orders;
`id` is a primary key, so two distinct competitors always differ there. The
order is therefore total, and comparing with `>` or `>=` gives the same winner.

## The direction of comparison matters

The signature is **ordered**: `A -> X` and `X -> A` of the same type are
different edges and both survive. Relationships here are directed, and
collapsing them would turn "Ada wrote the Notes" and "the Notes were written by
Ada" into one claim in whichever direction happened to be seen first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from redstring.domain.consolidation import RelationshipRedirection
from redstring.domain.preference import relationship_preference

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from redstring.domain.ids import EntityId
    from redstring.domain.relationship import Relationship

type _Signature = tuple[EntityId, EntityId, str]


def duplicate_preference(relationship: Relationship) -> tuple[float, int, str, str]:
    """A **total** order over edges competing for one signature. Higher wins.

    See the module docstring for why the id is appended and why that makes it
    total. The id is compared as its canonical lowercase hyphenated string, the
    same rendering `GraphStore`'s cursor order uses, so "which duplicate
    survived" and "which page an entity fell on" cannot disagree about how two
    UUIDs compare.
    """
    return (*relationship_preference(relationship), str(relationship.id))


def _signature(relationship: Relationship) -> _Signature:
    """What makes two edges the same claim. Directed -- see the docstring."""
    return (
        relationship.source_entity_id,
        relationship.target_entity_id,
        relationship.relationship_type,
    )


def plan_redirections(
    *,
    canonical_entity_id: EntityId,
    merged_entity_ids: Collection[EntityId],
    relationships: Sequence[Relationship],
) -> list[RelationshipRedirection]:
    """Every edge this merge moves or drops.

    Args:
        canonical_entity_id: The entity that survives.
        merged_entity_ids: The entities absorbed into it.
        relationships: Every edge touching the canonical entity *or* any
            absorbed one -- what `GraphStore.get_relationships_for` returns for
            the whole group. Edges touching neither are ignored rather than
            rejected, so a caller may pass a superset.

    Returns:
        One `RelationshipRedirection` per edge that changes, ordered by
        `before.id` as a string so the payload is stable whatever order the
        store returned.

        An edge already in its final shape and not duplicated produces
        **nothing**. A redirection whose `after` equals its `before` is a no-op
        the projection would apply and undo would "restore", and both are noise
        in a permanent event log.

    A duplicate that the canonical entity *already had* can itself be the one
    dropped, if an absorbed entity's edge outranks it. That is deliberate: the
    surviving edge is the better description of the claim, not the one that
    happened to belong to the winner.
    """
    absorbed = set(merged_entity_ids)
    group = {canonical_entity_id, *absorbed}

    def redirect(entity_id: EntityId) -> EntityId:
        return canonical_entity_id if entity_id in absorbed else entity_id

    relevant = [
        relationship
        for relationship in relationships
        if not group.isdisjoint((relationship.source_entity_id, relationship.target_entity_id))
    ]

    # Group by post-redirection signature. A self-loop has no signature to
    # compete for -- it cannot exist afterwards at all -- so it is dropped
    # outright rather than entered into the contest.
    dropped: list[Relationship] = []
    contenders: dict[_Signature, list[tuple[Relationship, Relationship]]] = {}
    for relationship in relevant:
        source = redirect(relationship.source_entity_id)
        target = redirect(relationship.target_entity_id)
        if source == target:
            dropped.append(relationship)
            continue
        moved = relationship.model_copy(
            update={"source_entity_id": source, "target_entity_id": target}
        )
        contenders.setdefault(_signature(moved), []).append((relationship, moved))

    redirections = [RelationshipRedirection(before=edge) for edge in dropped]
    for competing in contenders.values():
        # `max` over a total order, so the winner does not depend on the order
        # the store returned. `key` reads the *original* edge: `model_copy`
        # changes only the endpoints, and every field the order looks at is
        # identical on both, but reading the original keeps the comparison
        # about edges that exist rather than about ones being invented.
        winner = max(competing, key=lambda pair: duplicate_preference(pair[0]))
        for before, moved in competing:
            if before is not winner[0]:
                redirections.append(RelationshipRedirection(before=before))
            elif moved != before:
                redirections.append(RelationshipRedirection(before=before, after=moved))

    return sorted(redirections, key=lambda r: str(r.before.id))
