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

from pydantic import BaseModel, model_validator

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
