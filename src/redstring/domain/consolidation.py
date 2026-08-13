"""What a merge did to one edge, in enough detail to undo it.

A merge redirects the edges of the entities it absorbs onto the canonical
entity. `RelationshipRedirection` is the record of one such move: the edge as
it was, and the edge as it became.

`after is None` is not "nothing happened" -- it is **the edge was dropped**.
An edge whose *both* endpoints were absorbed by the same merge would become a
self-loop, which `Relationship` rejects outright, so the merge deletes it
instead of storing something the domain model forbids. Undo recreates it from
`before`, which is why `before` is the whole `Relationship` and not a pair of
endpoint ids: a recreated edge needs its type, confidence and properties back
as well.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_validator

from redstring.domain.ids import EntityId
from redstring.domain.relationship import Relationship


class RelationshipRedirection(BaseModel):
    """One edge, before and after a merge moved or dropped it."""

    before: Relationship
    after: Relationship | None = None

    @model_validator(mode="after")
    def _after_is_the_same_edge(self) -> RelationshipRedirection:
        """`after` must be the same edge, moved -- not a different one.

        A redirection is applied to a store by upserting `after` over the id
        it shares with `before`. If the two ids could differ, applying the
        redirection would create a second edge and leave the original in
        place, and undoing it by upserting `before` would not remove that
        second edge -- so the undo would silently be a no-op on half the
        change. Same reasoning for `tenant_id`, where the leak crosses a
        tenant boundary.
        """
        if self.after is None:
            return self
        if self.after.id != self.before.id:
            raise ValueError(
                f"after must describe the same relationship as before: "
                f"{self.after.id} != {self.before.id}"
            )
        if self.after.tenant_id != self.before.tenant_id:
            raise ValueError(
                f"after must belong to the same tenant as before: "
                f"{self.after.tenant_id} != {self.before.tenant_id}"
            )
        return self


class MergeableFields(BaseModel):
    """Exactly the `Entity` fields a merge may decide.

    Held as a value object rather than as three fields on the event because it
    appears twice -- as a merge's `after` and as an undo's restoration -- and
    the two must not be able to drift apart.
    """

    description: str | None = None
    external_ids: dict[str, str] = {}
    properties: dict[str, Any] = {}


class PropertyResolution(BaseModel):
    """What a merge decided about one entity's fields, before and after.

    ## Why one entity, when a merge combines several

    A merge does not touch the entities it absorbs. `GraphStore` has no
    `delete_entity` (ADR 0002), the projection writes an `Alias` per absorbed
    entity and nothing else, and those rows survive unchanged. So the whole
    effect of a merge on entity data is one before/after pair on the canonical
    entity, and an undo restores it by upserting `before`.

    BACKLOG B127 asked for every absorbed entity's originals here, reasoning
    that a `UNION` result cannot say who claimed what. True, and not needed:
    nothing downstream has a row to put them back into.

    ## `after` is the complete post-merge value, not a diff

    The projection replaces all three fields wholesale, so a key omitted from
    `after` is a key *deleted*. A resolution must therefore be exhaustive over
    the union of the group's keys rather than over what changed.
    """

    entity_id: EntityId
    before: MergeableFields
    after: MergeableFields
