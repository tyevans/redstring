"""Neo4j `GraphStore`: the second adapter, and the test of the port.

Every Cypher string in this library lives here. The port speaks domain types,
so nothing above this module needs to know a graph database is involved.

## Three decisions worth knowing before reading the queries

**No apoc.** The old `graph/client.py` -- deleted in slice 9, recoverable from
`3502900` -- called `apoc.create.addLabels` to give each entity a per-type
label and `apoc.path.subgraphAll` to traverse. Neither is needed:
`entity_type` is already an indexed property, and traversal is a
variable-length path. Requiring a plugin narrows which managed Neo4j offerings
can host this library, so plain Cypher wins where it costs nothing.

**One relationship type, `:RELATES_TO`, carrying `relationship_type` as a
property.** A native per-type edge label would need either apoc or Cypher's
dynamic-type syntax, and it buys nothing here: the port's only type operation
is an equality filter, which a property index serves. It also keeps the
relationship-uniqueness index single-shaped.

**JSON for the nested fields.** A Neo4j property is a primitive or a
*homogeneous* array -- it cannot hold `{"a": {"b": [1, "two"]}}`, an empty
dict, or an integer past 64 bits, all of which `Entity.properties` legitimately
contains. `properties`, `external_ids` and `temporal` are therefore stored as
JSON text, which round-trips nesting, types and empty containers exactly. The
fields the port *queries* on -- `normalized_name` and `entity_type` -- stay
native and indexed. `blocking_keys` is native too, but it is not what serves
the lookup: a list property cannot be seeked, so the keys are *also* nodes.
See `KEY_NODE`.

## Isolation comes free

The port requires that mutating a read result cannot change stored state. This
adapter gets that for nothing: every read decodes fresh domain objects out of
records, so there is no stored object to hand out. That is the one respect in
which a real database is easier to be correct in than a dictionary.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Self, cast
from uuid import UUID

from neo4j import AsyncGraphDatabase

from redstring.domain.alias import Alias
from redstring.domain.entity import Entity, ExtractionMethod
from redstring.domain.exceptions import AliasCycleError, MissingEntityError
from redstring.domain.relationship import Relationship
from redstring.domain.temporal import TemporalExtent

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from neo4j import AsyncDriver, Record
    from neo4j.graph import Node
    from neo4j.graph import Relationship as Edge

    from redstring.domain.ids import EntityId, RelationshipId, TenantId

#: The single relationship type. See the module docstring.
EDGE = "RELATES_TO"

#: Aliases live on their own nodes rather than on `:Entity`.
#:
#: Two reasons, both from the port. An alias may name an entity this tenant has
#: not extracted yet -- the merge fold must not depend on the extraction fold
#: having run -- so it cannot be an edge between `:Entity` nodes. And
#: resolution is transitive, which wants a variable-length path, which wants an
#: edge rather than a property.
ALIAS_NODE = "AliasRef"
ALIAS_EDGE = "ALIAS_OF"

#: Blocking keys are nodes, not a list property. This was BACKLOG B10b.
#:
#: A Neo4j range index over a list property indexes **the list as a single
#: value**, so it answers "which entities have exactly this array" and cannot
#: answer membership. Measured on 5000 entities across 100 tenants, the plan
#: for `$key IN e.blocking_keys` was `NodeByLabelScan` + `Filter` with and
#: without such an index -- identical -- which is why slice 4 correctly created
#: none. Consolidation fetches a block *per entity*, so a lookup that scans the
#: tenant is O(n) per entity and O(n^2) across one.
#:
#: The rejected alternative, so it is not revisited: a full-text index does
#: work on arrays but **tokenises**, and blocking keys are opaque identifiers
#: (`"A430"`, `"person:ad"`) that must match exactly.
#:
#: The property survives alongside the nodes. It is what `_entity_from`
#: decodes, and it is the only place "no keys known" (`None`) and "known to
#: have none" (empty) stay distinguishable -- an edge set cannot express that
#: difference.
KEY_NODE = "BlockingKey"
KEY_EDGE = "BLOCKED_BY"

_DIRECTIONS = ("out", "in", "both")

#: Cypher patterns for each `direction`, relative to the anchored entity `e`.
_PATTERNS: dict[str, str] = {
    "out": f"(e)-[r:{EDGE}]->()",
    "in": f"(e)<-[r:{EDGE}]-()",
    # Undirected between *distinct* nodes yields each edge once, and
    # `Relationship` forbids self-loops, so this cannot double-count.
    "both": f"(e)-[r:{EDGE}]-()",
}

#: Run once per connect. `IF NOT EXISTS` makes each idempotent.
#:
#: The uniqueness constraint is composite because two tenants may legitimately
#: hold the same entity id -- a constraint on `id` alone would reject the
#: second write, and that arrangement is exactly what the isolation properties
#: exercise. Every index leads with `tenant_id` for the same reason every query
#: filters on it: there is no cross-tenant read, so there is no useful index
#: that does not start there.
_SCHEMA = (
    "CREATE CONSTRAINT entity_tenant_id_unique IF NOT EXISTS "
    "FOR (e:Entity) REQUIRE (e.tenant_id, e.id) IS UNIQUE",
    "CREATE INDEX entity_tenant_normalized_name IF NOT EXISTS "
    "FOR (e:Entity) ON (e.tenant_id, e.normalized_name)",
    "CREATE INDEX entity_tenant_type IF NOT EXISTS FOR (e:Entity) ON (e.tenant_id, e.entity_type)",
    # There is deliberately no index for the `blocking_keys` *property*; see
    # KEY_NODE above for why one cannot serve a membership test. Lookups go
    # through `:BlockingKey` nodes, whose constraint is below.
    #
    # Relationship *uniqueness* constraints are an enterprise feature, so
    # by-id lookup is served by an index and enforced by the upsert query,
    # which deletes any existing edge of that id before creating the new one.
    f"CREATE INDEX relationship_tenant_id IF NOT EXISTS "
    f"FOR ()-[r:{EDGE}]-() ON (r.tenant_id, r.id)",
    # Unique rather than merely indexed: `upsert_alias` MERGEs on this pair
    # from two places in one query, and a duplicate node would fork the chain
    # so that resolution's answer depended on which copy it walked into.
    f"CREATE CONSTRAINT alias_ref_tenant_entity_unique IF NOT EXISTS "
    f"FOR (a:{ALIAS_NODE}) REQUIRE (a.tenant_id, a.entity_id) IS UNIQUE",
    # Unique, so the `MERGE` in `_write_blocking_keys` cannot race two
    # concurrent upserts into two nodes for one key -- which would make
    # `find_by_blocking_key` return whichever half the planner reached.
    f"CREATE CONSTRAINT blocking_key_tenant_key_unique IF NOT EXISTS "
    f"FOR (k:{KEY_NODE}) REQUIRE (k.tenant_id, k.key) IS UNIQUE",
)

#: A predicate that is always true, added to make the planner use the
#: `(tenant_id, id)` index.
#:
#: `id` is part of the uniqueness constraint, so it is never null and this
#: changes no result. It changes the *plan*: measured on 5000 entities across
#: 100 tenants, `MATCH (e:Entity {tenant_id: $t}) RETURN e ORDER BY e.id`
#: planned as `NodeByLabelScan` + `Filter` -- reading every entity of every
#: tenant -- while the same query with this clause planned as
#: `NodeUniqueIndexSeek`. Without it, a tenant-scoped read costs the whole
#: database, which is the difference between multi-tenancy working and not.
#:
#: Only used where no other indexed predicate is present. Alongside a
#: `normalized_name` or `entity_type` equality the planner already seeks the
#: better index, and this clause would only add a filter step.
_TENANT_SEEK = "e.id IS NOT NULL"


class Neo4jGraphStore:
    """A `GraphStore` backed by Neo4j, over the async driver."""

    def __init__(self, driver: AsyncDriver, *, database: str | None = None) -> None:
        """Wrap an existing driver. `close()` will not close it.

        Ownership follows who created the driver: a caller that injected one
        keeps the right to close it. `connect()` builds its own and does close
        it. Without that split, disposing a store per hypothesis example would
        take the shared connection pool down with the first one.
        """
        self._driver = driver
        self._database = database
        self._owns_driver = False

    @classmethod
    def connect(cls, uri: str, *, auth: tuple[str, str], database: str | None = None) -> Self:
        """Build a store owning a driver of its own, which `close()` closes."""
        store = cls(AsyncGraphDatabase.driver(uri, auth=auth), database=database)
        store._owns_driver = True
        return store

    async def close(self) -> None:
        """Release the driver, if this store created it."""
        if self._owns_driver:
            await self._driver.close()

    async def __aenter__(self) -> Self:
        """Enter a block whose exit closes this store. See `__aexit__`."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close on the way out, and **never suppress**.

        The `None` return is the decision, not an omission: `__aexit__` is
        read for truthiness, so any truthy value would swallow whatever the
        body raised -- including `CancelledError`, which would break task
        cancellation for the caller. `None` is falsy, so the exception
        propagates and this is a resource-release block rather than an
        exception handler.

        Closing goes through `close()`, so ownership still decides: a store
        wrapping an injected driver leaves it open here exactly as it does
        there.
        """
        await self.close()

    async def ensure_schema(self) -> None:
        """Create the constraint and indexes. Idempotent; safe on every start."""
        for statement in _SCHEMA:
            await self._run(statement)

    async def _run(self, query: str, /, **parameters: object) -> list[Record]:
        """Run one query and drain it. The only path to the database.

        Parameters are `object` rather than `Any` so a mistyped value is a
        type error at the call site instead of being silently accepted -- the
        driver serialises whatever it is given.
        """
        async with self._driver.session(database=self._database) as session:
            # `dict` is invariant, so `dict[str, object]` is not a
            # `dict[str, Any]`; the cast is at the driver boundary, which is
            # where static typing stops being useful anyway.
            result = await session.run(query, cast("dict[str, Any]", parameters))
            return [record async for record in result]

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    async def upsert_entity(self, entity: Entity) -> None:
        await self.upsert_entities([entity])

    async def upsert_entities(self, entities: Sequence[Entity]) -> None:
        if not entities:
            return
        # Deduplicated in Python, last write winning, because a single UNWIND
        # gives no ordering guarantee between a row's write and a later row's
        # read of the same node -- Neo4j's "eager" problem. Two rows for one
        # id in one batch is exactly what `upsert_entities([e, e])` sends.
        rows = list({(e.tenant_id, e.id): _entity_row(e) for e in entities}.values())
        await self._run(
            "UNWIND $rows AS row "
            "MERGE (e:Entity {tenant_id: row.tenant_id, id: row.id}) "
            # `SET e = row` replaces the property set wholesale, which is what
            # last-write-wins means: a field absent from the new value must not
            # survive from the old one. Null entries in `row` remove theirs.
            "SET e = row",
            rows=rows,
        )
        await self._write_blocking_keys(rows)

    async def _write_blocking_keys(self, rows: list[dict[str, Any]]) -> None:
        """Rebuild each entity's `:BLOCKED_BY` edges from its `blocking_keys`.

        **The second write path B10b names, and the reason this was not a
        one-line change.** A re-upsert must delete the entity's *previous* key
        edges or a stale key keeps matching, and `find_by_blocking_key` starts
        returning entities that no longer carry the key --
        `test_find_by_blocking_key_reflects_the_latest_write` is the test that
        holds this honest.

        Two statements rather than one. Delete-then-create in a single query
        needs `WITH DISTINCT` to undo the row multiplication `OPTIONAL MATCH`
        causes over existing edges, and then reads as if the distinctness were
        about the keys rather than about the plan. Two statements each say one
        thing.

        Every row is processed, including those whose `blocking_keys` is null.
        An entity going from "has keys" to "has none" must lose its edges just
        as much as one going from one key to another, and it is the case a
        create-only implementation gets wrong.
        """
        await self._run(
            "UNWIND $rows AS row "
            "MATCH (e:Entity {tenant_id: row.tenant_id, id: row.id})"
            f"-[old:{KEY_EDGE}]->() "
            "DELETE old",
            rows=rows,
        )
        keyed = rows_carrying_keys(rows)
        if not keyed:
            return
        await self._run(
            "UNWIND $rows AS row "
            "MATCH (e:Entity {tenant_id: row.tenant_id, id: row.id}) "
            "UNWIND row.blocking_keys AS key "
            f"MERGE (k:{KEY_NODE} {{tenant_id: row.tenant_id, key: key}}) "
            f"MERGE (e)-[:{KEY_EDGE}]->(k)",
            rows=keyed,
        )

    async def get_entity(self, entity_id: EntityId, tenant_id: TenantId) -> Entity | None:
        records = await self._run(
            "MATCH (e:Entity {tenant_id: $tenant_id, id: $entity_id}) RETURN e",
            tenant_id=str(tenant_id),
            entity_id=str(entity_id),
        )
        return _entity_from(records[0]["e"]) if records else None

    async def get_entities(
        self, entity_ids: Sequence[EntityId], tenant_id: TenantId
    ) -> list[Entity]:
        if not entity_ids:
            return []
        # `dict.fromkeys` deduplicates: a repeated id must yield one entity,
        # and UNWIND would otherwise emit a row per occurrence.
        wanted = [str(entity_id) for entity_id in dict.fromkeys(entity_ids)]
        records = await self._run(
            "UNWIND $entity_ids AS wanted "
            "MATCH (e:Entity {tenant_id: $tenant_id, id: wanted}) "
            "RETURN e",
            tenant_id=str(tenant_id),
            entity_ids=wanted,
        )
        return [_entity_from(record["e"]) for record in records]

    async def find_entities(
        self,
        tenant_id: TenantId,
        *,
        name: str | None = None,
        entity_type: str | None = None,
        limit: int | None = None,
        after: EntityId | None = None,
    ) -> list[Entity]:
        if limit is not None and limit < 0:
            raise ValueError("limit must not be negative")

        # Filters are appended rather than written as `$name IS NULL OR ...`,
        # which would defeat the index: a predicate the planner cannot resolve
        # until runtime forces a label scan even when a seek was available.
        clauses = []
        parameters: dict[str, Any] = {"tenant_id": str(tenant_id)}
        if name is not None:
            clauses.append("e.normalized_name = $name")
            parameters["name"] = name
        if entity_type is not None:
            clauses.append("e.entity_type = $entity_type")
            parameters["entity_type"] = entity_type
        if after is not None:
            # Strictly greater, on the canonical string, so the cursor stays
            # valid when the id it names has since been deleted.
            clauses.append("e.id > $after")
            parameters["after"] = str(after)

        query = "MATCH (e:Entity {tenant_id: $tenant_id}) "
        query += "WHERE " + " AND ".join(clauses or [_TENANT_SEEK]) + " "
        # Ascending canonical-string id, the total order the port documents.
        query += "RETURN e ORDER BY e.id"
        if limit is not None:
            query += " LIMIT $limit"
            parameters["limit"] = limit

        return [_entity_from(record["e"]) for record in await self._run(query, **parameters)]

    async def find_by_blocking_key(self, key: str, tenant_id: TenantId) -> list[Entity]:
        # Anchored on the key node, which the uniqueness constraint indexes, so
        # this is a seek to one node and an expansion over its edges -- not a
        # scan of the tenant. That is the whole of B10b: consolidation asks
        # this once per entity, so a tenant scan here is O(n^2) across a
        # tenant.
        records = await self._run(
            f"MATCH (k:{KEY_NODE} {{tenant_id: $tenant_id, key: $key}})"
            f"<-[:{KEY_EDGE}]-(e:Entity) "
            "RETURN e ORDER BY e.id",
            tenant_id=str(tenant_id),
            key=key,
        )
        return [_entity_from(record["e"]) for record in records]

    async def find_by_blocking_keys(
        self, keys: Sequence[str], tenant_id: TenantId
    ) -> dict[str, list[Entity]]:
        # Seeded with every requested key so absent ones map to [] rather than
        # being missing -- the caller iterates the result, not its request.
        grouped: dict[str, list[Entity]] = {key: [] for key in keys}
        if not grouped:
            return grouped

        records = await self._run(
            "UNWIND $keys AS wanted "
            f"MATCH (k:{KEY_NODE} {{tenant_id: $tenant_id, key: wanted}})"
            f"<-[:{KEY_EDGE}]-(e:Entity) "
            "RETURN wanted AS key, e ORDER BY key, e.id",
            tenant_id=str(tenant_id),
            keys=list(grouped),
        )
        for record in records:
            grouped[record["key"]].append(_entity_from(record["e"]))
        return grouped

    # ------------------------------------------------------------------
    # Aliases
    # ------------------------------------------------------------------

    async def upsert_alias(self, alias: Alias) -> None:
        await self._run(
            # `MERGE` both ends: the port says neither entity need exist, so a
            # `MATCH (:Entity)` here would silently drop the write for an alias
            # recorded before the extraction that creates its entities is
            # folded -- which is exactly the ordering aliases exist to survive.
            f"MERGE (a:{ALIAS_NODE} {{tenant_id: $tenant_id, entity_id: $alias_entity_id}}) "
            f"MERGE (c:{ALIAS_NODE} {{tenant_id: $tenant_id, entity_id: $canonical_entity_id}}) "
            "WITH a, c "
            # At most one canonical parent per entity, which is the store's
            # half of `ConsolidationLog`'s double-merge rule. Deleting the
            # previous edge by pattern rather than upserting it in place is
            # what makes a re-record with a *different* canonical replace
            # rather than fork the chain.
            f"OPTIONAL MATCH (a)-[old:{ALIAS_EDGE}]->() "
            "DELETE old "
            "WITH a, c "
            f"CREATE (a)-[r:{ALIAS_EDGE}]->(c) "
            "SET r = $row",
            tenant_id=str(alias.tenant_id),
            alias_entity_id=str(alias.alias_entity_id),
            canonical_entity_id=str(alias.canonical_entity_id),
            row=_alias_row(alias),
        )

    async def remove_alias(self, alias_entity_id: EntityId, tenant_id: TenantId) -> bool:
        # The `:AliasRef` node survives with no outgoing edge, which resolution
        # reads as "not an alias" -- the same answer as no node at all. Leaving
        # it costs one empty node and avoids a delete that would have to check
        # for incoming edges first; `delete_by_tenant` reaps them.
        records = await self._run(
            f"MATCH (a:{ALIAS_NODE} {{tenant_id: $tenant_id, entity_id: $alias_entity_id}})"
            f"-[r:{ALIAS_EDGE}]->() "
            "DELETE r "
            "RETURN count(*) AS removed",
            tenant_id=str(tenant_id),
            alias_entity_id=str(alias_entity_id),
        )
        return bool(records[0]["removed"])

    async def find_aliases(self, canonical_entity_id: EntityId, tenant_id: TenantId) -> list[Alias]:
        records = await self._run(
            f"MATCH (a:{ALIAS_NODE} {{tenant_id: $tenant_id}})-[r:{ALIAS_EDGE}]->"
            f"(:{ALIAS_NODE} {{tenant_id: $tenant_id, entity_id: $canonical_entity_id}}) "
            # One hop, not `*1..`: the question is what this merge absorbed.
            "RETURN r ORDER BY a.entity_id",
            tenant_id=str(tenant_id),
            canonical_entity_id=str(canonical_entity_id),
        )
        return [_alias_from(record["r"]) for record in records]

    async def resolve_entity_ids(
        self, entity_ids: Sequence[EntityId], tenant_id: TenantId
    ) -> dict[EntityId, EntityId]:
        if not entity_ids:
            return {}

        wanted = list(dict.fromkeys(entity_ids))
        records = await self._run(
            "UNWIND $entity_ids AS wanted "
            f"OPTIONAL MATCH (a:{ALIAS_NODE} {{tenant_id: $tenant_id, entity_id: wanted}}) "
            # Variable-length, because chains form: merging `B` into `A` and
            # then `A` into `C` is two legal merges and `B` must give `C`.
            # Cypher's relationship-uniqueness rule terminates a cycle, so this
            # cannot hang -- it returns nothing, which is what `is_alias`
            # below turns into a loud error rather than a wrong answer.
            f"OPTIONAL MATCH (a)-[:{ALIAS_EDGE}*1..]->(c:{ALIAS_NODE}) "
            f"WHERE NOT EXISTS {{ (c)-[:{ALIAS_EDGE}]->() }} "
            "RETURN wanted, "
            "  c.entity_id AS canonical, "
            f"  EXISTS {{ (a)-[:{ALIAS_EDGE}]->() }} AS is_alias",
            tenant_id=str(tenant_id),
            entity_ids=[str(entity_id) for entity_id in wanted],
        )

        by_id = {record["wanted"]: record for record in records}
        resolved: dict[EntityId, EntityId] = {}
        for entity_id in wanted:
            record = by_id[str(entity_id)]
            if record["canonical"] is not None:
                resolved[entity_id] = UUID(record["canonical"])
            elif record["is_alias"]:
                # It has an outgoing edge but no chain end: a cycle.
                raise AliasCycleError(entity_id=entity_id, tenant_id=tenant_id)
            else:
                resolved[entity_id] = entity_id
        return resolved

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    async def upsert_relationship(self, relationship: Relationship) -> None:
        await self.upsert_relationships([relationship])

    async def upsert_relationships(self, relationships: Sequence[Relationship]) -> None:
        if not relationships:
            return
        deduplicated = list({(r.tenant_id, r.id): r for r in relationships}.values())
        await self._reject_dangling(deduplicated)

        records = await self._run(
            "UNWIND $rows AS row "
            "MATCH (s:Entity {tenant_id: row.tenant_id, id: row.source_entity_id}) "
            "MATCH (t:Entity {tenant_id: row.tenant_id, id: row.target_entity_id}) "
            # An upsert may redirect an edge onto different endpoints, so the
            # existing edge cannot be found by matching the new pattern -- it
            # is found by id, through the relationship index, and removed. That
            # is what makes re-upserting an id replace rather than duplicate.
            f"OPTIONAL MATCH ()-[old:{EDGE} "
            "{tenant_id: row.tenant_id, id: row.id}]->() "
            "DELETE old "
            "WITH row, s, t "
            f"CREATE (s)-[r:{EDGE}]->(t) "
            "SET r = row "
            # The write reports what it wrote, because `MATCH` drops a row
            # whose endpoint is absent *silently*. The check above and this
            # write are separate implicit transactions -- `_run` opens a
            # session per query -- so an endpoint deleted in between would
            # otherwise leave the caller told a batch succeeded that was
            # never written. The port's contract here is write-or-raise.
            "RETURN r.tenant_id AS tenant_id, r.id AS id",
            rows=[_relationship_row(r) for r in deduplicated],
        )
        # Keyed on the pair, because that is what identifies a relationship
        # here -- the same key the deduplication above uses. Keyed on `id`
        # alone, one tenant's successful write would vouch for another
        # tenant's dropped row carrying the same id, which defeats the check
        # in exactly the case it exists for.
        written = {(record["tenant_id"], record["id"]) for record in records}
        missing = [r for r in deduplicated if (str(r.tenant_id), str(r.id)) not in written]
        if missing:
            # Normal path: re-checking names *which* endpoint went away, and
            # the error is the same one a dangling edge raises up front.
            await self._reject_dangling(missing)
            # Reached only if the endpoint reappeared between the failed write
            # and this re-check. The rows are still unwritten, so raising is
            # right even though no endpoint is absent to name any more.
            raise MissingEntityError(
                entity_id=missing[0].source_entity_id, tenant_id=missing[0].tenant_id
            )

    async def _reject_dangling(self, relationships: Sequence[Relationship]) -> None:
        """Raise `MissingEntityError` for the first absent endpoint.

        Separate from the write because the error must name *which* endpoint is
        missing, and a write query cannot report that while also writing. It is
        one query for the whole batch, not one per edge.
        """
        needed = {
            (r.tenant_id, endpoint)
            for r in relationships
            for endpoint in (r.source_entity_id, r.target_entity_id)
        }
        records = await self._run(
            "UNWIND $pairs AS pair "
            "MATCH (e:Entity {tenant_id: pair.tenant_id, id: pair.id}) "
            "RETURN e.tenant_id AS tenant_id, e.id AS id",
            pairs=[{"tenant_id": str(t), "id": str(e)} for t, e in needed],
        )
        present = {(record["tenant_id"], record["id"]) for record in records}
        # Iterated in the caller's order, and source before target within an
        # edge, so which endpoint is reported is deterministic.
        for relationship in relationships:
            for endpoint in (relationship.source_entity_id, relationship.target_entity_id):
                if (str(relationship.tenant_id), str(endpoint)) not in present:
                    raise MissingEntityError(entity_id=endpoint, tenant_id=relationship.tenant_id)

    async def get_relationships(
        self,
        entity_id: EntityId,
        tenant_id: TenantId,
        *,
        direction: Literal["out", "in", "both"] = "both",
        relationship_types: Sequence[str] | None = None,
    ) -> list[Relationship]:
        return await self.get_relationships_for(
            [entity_id], tenant_id, direction=direction, relationship_types=relationship_types
        )

    async def get_relationships_for(
        self,
        entity_ids: Sequence[EntityId],
        tenant_id: TenantId,
        *,
        direction: Literal["out", "in", "both"] = "both",
        relationship_types: Sequence[str] | None = None,
    ) -> list[Relationship]:
        if direction not in _DIRECTIONS:
            raise ValueError(f"direction must be 'out', 'in' or 'both', not {direction!r}")
        if not entity_ids:
            return []

        records = await self._run(
            "UNWIND $entity_ids AS wanted "
            "MATCH (e:Entity {tenant_id: $tenant_id, id: wanted}) "
            f"MATCH {_PATTERNS[direction]} "
            "WHERE r.tenant_id = $tenant_id "
            "  AND ($any_type OR r.relationship_type IN $relationship_types) "
            # An edge with both endpoints in `entity_ids` is matched twice; the
            # result is a set of edges, so it must appear once.
            "RETURN DISTINCT r ORDER BY r.id",
            tenant_id=str(tenant_id),
            entity_ids=[str(entity_id) for entity_id in dict.fromkeys(entity_ids)],
            any_type=relationship_types is None,
            relationship_types=list(relationship_types or ()),
        )
        return [_relationship_from(record["r"]) for record in records]

    async def delete_relationship(
        self, relationship_id: RelationshipId, tenant_id: TenantId
    ) -> bool:
        records = await self._run(
            f"MATCH ()-[r:{EDGE} {{tenant_id: $tenant_id, id: $relationship_id}}]->() "
            "DELETE r "
            "RETURN count(*) AS removed",
            tenant_id=str(tenant_id),
            relationship_id=str(relationship_id),
        )
        return bool(records[0]["removed"])

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    async def neighbors(
        self,
        entity_id: EntityId,
        tenant_id: TenantId,
        *,
        depth: int = 1,
        relationship_types: Sequence[str] | None = None,
    ) -> list[Entity]:
        # A variable-length pattern cannot take a parameter, so `depth` is the
        # one value in this module formatted into a query string. It is proved
        # to be a plain integer first -- `bool` excluded because it is an `int`
        # subclass that would render as `True`.
        if isinstance(depth, bool) or not isinstance(depth, int):
            raise TypeError(f"depth must be an int, not {type(depth).__name__}")
        if depth < 0:
            raise ValueError("depth must not be negative")
        if depth == 0:
            # `*1..0` is not a legal pattern, and the answer is [] regardless.
            return []

        records = await self._run(
            "MATCH (origin:Entity {tenant_id: $tenant_id, id: $entity_id}) "
            # One variable-length path rather than `depth` rounds of
            # expansion. Cypher's relationship-uniqueness rule -- no edge
            # twice in one path -- is what terminates a cycle.
            f"MATCH (origin)-[rels:{EDGE}*1..{depth}]-(e:Entity) "
            "WHERE e.tenant_id = $tenant_id "
            "  AND e.id <> $entity_id "
            "  AND all(rel IN rels WHERE rel.tenant_id = $tenant_id "
            "      AND ($any_type OR rel.relationship_type IN $relationship_types)) "
            "RETURN DISTINCT e ORDER BY e.id",
            tenant_id=str(tenant_id),
            entity_id=str(entity_id),
            any_type=relationship_types is None,
            relationship_types=list(relationship_types or ()),
        )
        return [_entity_from(record["e"]) for record in records]

    # ------------------------------------------------------------------
    # Tenant lifecycle
    # ------------------------------------------------------------------

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        # DETACH removes the tenant's edges with its nodes, so no orphan
        # survives to be resurrected when the entities are written again.
        records = await self._run(
            "MATCH (e:Entity {tenant_id: $tenant_id}) DETACH DELETE e RETURN count(e) AS removed",
            tenant_id=str(tenant_id),
        )
        # Separate statement, and counted separately: the port's return value
        # is entities removed, and alias bookkeeping is not an entity. Without
        # this, a wiped tenant replays its merges over aliases that survived,
        # so `delete_by_tenant` stops being a reset in exactly the case a
        # rebuild needs it to be one.
        await self._run(
            f"MATCH (a:{ALIAS_NODE} {{tenant_id: $tenant_id}}) DETACH DELETE a",
            tenant_id=str(tenant_id),
        )
        # The `DETACH` above took this tenant's `:BLOCKED_BY` edges with its
        # entities, but the key nodes themselves are not entities and survive.
        # An orphan `:BlockingKey` matches nothing, so leaving it would be
        # correct and would still leak one node per distinct key per wiped
        # tenant, forever.
        await self._run(
            f"MATCH (k:{KEY_NODE} {{tenant_id: $tenant_id}}) DETACH DELETE k",
            tenant_id=str(tenant_id),
        )
        return int(records[0]["removed"])


# ----------------------------------------------------------------------
# Encoding
#
# A Neo4j property is a primitive or a homogeneous array. Everything that is
# neither becomes JSON text; everything the port queries on stays native.
# ----------------------------------------------------------------------


def _entity_row(entity: Entity) -> dict[str, Any]:
    return {
        "tenant_id": str(entity.tenant_id),
        "id": str(entity.id),
        "name": entity.name,
        "normalized_name": entity.normalized_name,
        "entity_type": entity.entity_type,
        "original_entity_type": entity.original_entity_type,
        "description": entity.description,
        "source_id": entity.source_id,
        "source_text": entity.source_text,
        "extraction_method": entity.extraction_method.value,
        "model": entity.model,
        "confidence": entity.confidence,
        # A list, so `find_by_blocking_key` can filter on it in Cypher. Sorted
        # for a stable stored form; the domain type is a set either way.
        # `None` is left as null (Neo4j drops the property) while an empty set
        # is stored as an empty array, which keeps "no keys known" and "known
        # to have none" distinguishable on the way back.
        "blocking_keys": None if entity.blocking_keys is None else sorted(entity.blocking_keys),
        "external_ids_json": json.dumps(entity.external_ids),
        "properties_json": json.dumps(entity.properties),
        "temporal_json": None if entity.temporal is None else entity.temporal.model_dump_json(),
    }


def _entity_from(node: Node) -> Entity:
    temporal = node.get("temporal_json")
    return Entity(
        id=UUID(node["id"]),
        tenant_id=UUID(node["tenant_id"]),
        name=node["name"],
        normalized_name=node["normalized_name"],
        entity_type=node["entity_type"],
        original_entity_type=node.get("original_entity_type"),
        description=node.get("description"),
        source_id=node.get("source_id"),
        source_text=node.get("source_text"),
        external_ids=json.loads(node["external_ids_json"]),
        properties=json.loads(node["properties_json"]),
        extraction_method=ExtractionMethod(node["extraction_method"]),
        model=node.get("model"),
        confidence=node["confidence"],
        temporal=None if temporal is None else TemporalExtent.model_validate_json(temporal),
        blocking_keys=(
            None if node.get("blocking_keys") is None else frozenset(node["blocking_keys"])
        ),
    )


def rows_carrying_keys(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows `_write_blocking_keys` has anything to create edges for.

    Public and pure so the default gate can test it: everything around it is
    Cypher and only runs when a server is up, and "which rows need the second
    statement" is the one decision in that method rather than plumbing.

    Truthiness, deliberately -- it must drop both `None` ("no keys known") and
    `[]` ("known to have none"). The two differ to `_entity_from`, which is why
    the property keeps them apart, but not here: neither creates an edge.
    """
    return [row for row in rows if row["blocking_keys"]]


def _alias_row(alias: Alias) -> dict[str, Any]:
    """Alias properties for the `:ALIAS_OF` edge.

    `merged_at` is ISO text rather than a native Neo4j `DateTime`, so the
    timezone survives verbatim: the driver returns a `neo4j.time.DateTime`
    whose conversion back is lossy for offsets Python spells differently, and
    the port compares `Alias`es for equality.
    """
    return {
        "id": str(alias.id),
        "tenant_id": str(alias.tenant_id),
        "canonical_entity_id": str(alias.canonical_entity_id),
        "alias_entity_id": str(alias.alias_entity_id),
        "alias_name": alias.alias_name,
        "alias_normalized_name": alias.alias_normalized_name,
        "merged_at": alias.merged_at.isoformat(),
        "merge_reason": alias.merge_reason,
    }


def _alias_from(edge: Edge) -> Alias:
    return Alias(
        id=UUID(edge["id"]),
        tenant_id=UUID(edge["tenant_id"]),
        canonical_entity_id=UUID(edge["canonical_entity_id"]),
        alias_entity_id=UUID(edge["alias_entity_id"]),
        # `.get`, because Neo4j drops a property written as null and both
        # names are legitimately absent -- the fold writes an alias with no
        # name when the absorbed entity's extraction has not been folded yet.
        alias_name=edge.get("alias_name"),
        alias_normalized_name=edge.get("alias_normalized_name"),
        merged_at=datetime.fromisoformat(edge["merged_at"]),
        merge_reason=edge.get("merge_reason"),
    )


def _relationship_row(relationship: Relationship) -> dict[str, Any]:
    return {
        "tenant_id": str(relationship.tenant_id),
        "id": str(relationship.id),
        # Redundant against the edge's own endpoints, and kept because it lets
        # a read decode a relationship from the edge alone -- no second match
        # for the nodes just to learn which way round it goes.
        "source_entity_id": str(relationship.source_entity_id),
        "target_entity_id": str(relationship.target_entity_id),
        "relationship_type": relationship.relationship_type,
        "source_id": relationship.source_id,
        "properties_json": json.dumps(relationship.properties),
        "confidence": relationship.confidence,
    }


def _relationship_from(edge: Edge) -> Relationship:
    return Relationship(
        id=UUID(edge["id"]),
        tenant_id=UUID(edge["tenant_id"]),
        source_entity_id=UUID(edge["source_entity_id"]),
        target_entity_id=UUID(edge["target_entity_id"]),
        relationship_type=edge["relationship_type"],
        # `.get`, not `[]`: Neo4j drops a property written as null, and an
        # edge written before this field existed has none either.
        source_id=edge.get("source_id"),
        properties=json.loads(edge["properties_json"]),
        confidence=edge["confidence"],
    )
