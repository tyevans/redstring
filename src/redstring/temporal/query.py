"""What a tenant's graph held over an interval.

## This composes over `GraphStore`; it did not get a port method

The capability the deleted `TimelineQueryService` provided is kept. Its shape
is not: it was built on the ORM and on job/project grouping that slices 1 and 8
removed, and the question a caller actually asks is "what did this tenant's
graph hold over this interval", which mentions neither.

That question could have become `GraphStore.find_entities(...,
temporal_overlaps=...)`. It did not, and the argument is not "composition is
tidier" -- it is that **the predicate is not a range test on two columns**:

- `precision` widens a bound. "2023" is `start_date=2023-01-01, end_date=None`,
  and denotes all of 2023. A `WHERE start_date >= ? AND end_date <= ?` misses
  it entirely, because `end_date` is null and the row means something the
  columns do not say.
- `UncertaintyMarker.BEFORE` and `AFTER` make a bound *infinite*, in opposite
  directions, from a field that is neither of the two date columns.

Both rules live in `domain.interval`. Pushing the predicate into the port means
writing them again in Cypher, again in the memory adapter, and again in any
future SQL adapter -- three copies of a rule that will diverge, silently,
because a wrong answer here looks exactly like a right one. The compliance
suite would have to grow a full interval-semantics conformance section to
catch it, which is a large amount of machinery to defend a decision that could
simply not be taken.

The cost is real and is not hidden: this pages the whole tenant and filters in
Python, so it is linear in entity count regardless of how few entities are
dated. **B48 records it**, along with the shape that fixes it when it stops
being acceptable -- a port method returning a deliberate *superset* from a
cheap indexed range scan, with `relate` still the exact filter over what comes
back. That keeps the semantics in one place and gives adapters only a range
scan, which they cannot get subtly wrong.

## Paging is bounded

`find_entities` is a cursor read whose exit condition comes from
adapter-supplied data. A cursor that fails to advance turns the loop into a
hang, and a hang in CI reads as infrastructure trouble and gets retried rather
than investigated. So the loop is bounded and fails with a message naming the
cause.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from redstring.domain.interval import TemporalRelation, bounds, relate_bounds
from redstring.temporal.inference import (
    DEFAULT_MAX_PAIRS,
    INFERRED_RELATIONS,
    InferredRelation,
    infer_relations,
    order_key,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection

    from redstring.domain.entity import Entity
    from redstring.domain.ids import TenantId
    from redstring.domain.interval import Bounds
    from redstring.ports.graph_store import GraphStore

#: Entities per `find_entities` call. Large enough that a modest tenant is one
#: or two round trips, small enough that a page is not a memory event.
DEFAULT_PAGE_SIZE: Final = 500

#: How many pages before the scan gives up. At the default page size this is
#: five million entities, which no tenant reaches by accident -- so reaching it
#: means the cursor stopped advancing, not that the tenant is large.
MAX_PAGES: Final = 10_000

#: The relations that count as "within the interval" when a caller does not
#: say. Everything except the two disjoint ones, i.e. "the extents intersect".
INTERSECTING: Final = frozenset(TemporalRelation) - {
    TemporalRelation.BEFORE,
    TemporalRelation.AFTER,
}


class CursorStalledError(RuntimeError):
    """A paged read stopped advancing. See the module docstring."""

    def __init__(self, tenant_id: TenantId, pages: int) -> None:
        super().__init__(
            f"scanning tenant {tenant_id} did not finish in {pages} pages. The "
            f"cursor is not advancing -- an adapter's find_entities is either "
            f"ignoring `after` or not ordering by id as the port requires."
        )


class TemporalQuery:
    """Timeline reads over any `GraphStore`."""

    def __init__(self, store: GraphStore, *, page_size: int = DEFAULT_PAGE_SIZE) -> None:
        """Assemble a query.

        Args:
            store: Where entities are read from. Any adapter; nothing here is
                Cypher-shaped or knows which one it has.
            page_size: Entities per round trip. A tuning knob, not a limit on
                the answer -- every method pages until the tenant is exhausted.

        Raises:
            ValueError: `page_size` is not positive, which would page forever.
        """
        if page_size < 1:
            raise ValueError(f"page_size must be at least 1, got {page_size}")
        self._store = store
        self._page_size = page_size

    async def entities_in_interval(
        self,
        tenant_id: TenantId,
        interval: Bounds,
        *,
        relations: Collection[TemporalRelation] = INTERSECTING,
        entity_type: str | None = None,
    ) -> list[Entity]:
        """This tenant's dated entities standing in `relations` to `interval`.

        Args:
            tenant_id: The only tenant read. There is no cross-tenant read.
            interval: The window asked about. Build it with
                `domain.interval.bounds`, or directly -- `Bounds(None, x)` for
                "anything up to x" and `Bounds(x, None)` for "x onwards".
            relations: How an entity's extent must stand to `interval` to
                count. The relation is read **entity-to-interval**, so
                `DURING` means "fell inside the window" and `CONTAINS` means
                "spanned it". Defaults to any intersection.
            entity_type: Restricts to one type, pushed down to the store.

        Returns:
            Matching entities, ascending by id -- the port's order, preserved
            rather than re-sorted, so a caller can page the result itself.
            Undated entities never match, including those carrying only a
            `sequence_position`.
        """
        wanted = frozenset(relations)
        found: list[Entity] = []
        async for entity in self._scan(tenant_id, entity_type=entity_type):
            extent = entity.temporal
            if extent is None:
                continue
            entity_bounds = bounds(extent)
            if entity_bounds is not None and relate_bounds(entity_bounds, interval) in wanted:
                found.append(entity)
        return found

    async def timeline(
        self,
        tenant_id: TenantId,
        *,
        interval: Bounds | None = None,
        entity_type: str | None = None,
    ) -> list[Entity]:
        """This tenant's dated entities in time order.

        Args:
            tenant_id: The only tenant read.
            interval: Restricts to entities intersecting this window. `None`
                for the whole timeline.
            entity_type: Restricts to one type.

        Returns:
            Entities ordered by when they begin, then by when they end, then
            by id. The id is not decoration: two entities routinely carry the
            same extent -- a document naming three things that happened in
            1066 -- and without it the order of those three would depend on
            what the store handed back, which the port does not promise to
            keep stable across adapters.

            Ordering is by *interval*, so an entity whose extent is open below
            precedes every dated one, and one open above follows every
            entity whose start is known.
        """
        found = (
            await self.entities_in_interval(tenant_id, interval, entity_type=entity_type)
            if interval is not None
            else [
                entity
                async for entity in self._scan(tenant_id, entity_type=entity_type)
                if entity.temporal is not None and bounds(entity.temporal) is not None
            ]
        )
        return sorted(found, key=_chronologically)

    async def relations_in_interval(
        self,
        tenant_id: TenantId,
        interval: Bounds | None = None,
        *,
        relations: Collection[TemporalRelation] = INFERRED_RELATIONS,
        entity_type: str | None = None,
        max_pairs: int = DEFAULT_MAX_PAIRS,
    ) -> list[InferredRelation]:
        """Temporal relations among this tenant's entities in `interval`.

        Computed, never stored -- see `redstring.temporal.inference` for why
        that is a decision rather than a convenience.

        Args:
            tenant_id: The only tenant read.
            interval: Restricts which entities take part. `None` for all of
                them, which on a large tenant is what `max_pairs` is for.
            relations: Which relations to return.
            entity_type: Restricts to one type.
            max_pairs: Refuse rather than grind; see `infer_relations`.

        Returns:
            One `InferredRelation` per related pair, sorted, with no inverses.
        """
        return infer_relations(
            await self.timeline(tenant_id, interval=interval, entity_type=entity_type),
            relations=relations,
            max_pairs=max_pairs,
        )

    async def _scan(self, tenant_id: TenantId, *, entity_type: str | None) -> AsyncIterator[Entity]:
        """Every entity of this tenant, one page at a time.

        Bounded, for the reason in the module docstring: the exit condition is
        `find_entities` returning a short page, which is adapter-supplied data.
        """
        cursor = None
        for _ in range(MAX_PAGES):
            page = await self._store.find_entities(
                tenant_id, entity_type=entity_type, limit=self._page_size, after=cursor
            )
            for entity in page:
                yield entity
            # A short page means the end. Exiting on an *empty* page instead
            # is equally correct and costs one more round trip -- a mutation
            # swapping them survives the suite, and understanding why is the
            # point rather than killing it.
            if len(page) < self._page_size:
                return
            cursor = page[-1].id
        raise CursorStalledError(tenant_id, MAX_PAGES)


def _chronologically(entity: Entity) -> tuple[int, str, int, str, str]:
    """Sort key: when it began, when it ended, then id. See `timeline`.

    Imported from `inference.py` rather than restated, so the timeline and
    the relations derived from it cannot disagree about what "earlier" means.
    """
    extent_bounds = bounds(entity.temporal) if entity.temporal is not None else None
    if extent_bounds is None:  # pragma: no cover - callers filter these out first
        raise ValueError(f"entity {entity.id} has no interval to order by")
    return order_key(extent_bounds, entity.id)
