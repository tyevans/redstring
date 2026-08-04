# ADR 0003: Blocking keys are Neo4j nodes, not a list property

**Status:** accepted, slice 7 of the ring migration. Was `BACKLOG` B10b.

**Why this is an ADR:** it is the one storage-layout decision in the migration
that was settled by measurement rather than by argument, the measurement is not
reproducible from anything in the tree, and the obvious cheaper alternative —
"add an index to the list property" — is one somebody will try. Without the
numbers this entry is an opinion.

## Context

Consolidation finds merge candidates by *blocking*: reduce each entity to a
small set of opaque keys (`"A430"`, `"person:ad"`), then only compare entities
that share one. The lookup is `find_by_blocking_key(key, tenant)`, and
consolidation calls it **once per entity**.

`Entity.blocking_keys` is a set of strings, and the natural Neo4j
representation is a list property on `:Entity`.

## The problem, measured

A Neo4j range index over a list property indexes **the list as a single
value**. It answers "which entities have exactly this array" and cannot answer
membership. Measured on 5000 entities across 100 tenants, the plan for
`$key IN e.blocking_keys` was `NodeByLabelScan` + `Filter` **with and without
such an index — identical**.

That row is the load-bearing one. Without it, this reads as "nobody got round
to indexing it" and invites someone to add the index and believe they fixed it.

So the lookup scans the tenant: O(n) per entity, and because consolidation asks
per entity, **O(n²) across a tenant**. It was acceptable while nothing called
it, and it stopped being acceptable in exactly the slice that started calling
it.

**A full-text index was considered and rejected.** It does work on arrays, but
it *tokenises*, and blocking keys are opaque identifiers that must match
exactly.

## Decision

Blocking keys become `(:BlockingKey)` nodes, joined to entities by
`[:BLOCKED_BY]`, with a uniqueness constraint on `(tenant_id, key)`. The
lookup seeks the key node and expands its edges.

The list property **survives alongside the nodes**. It is what `_entity_from`
decodes, and it is the only place where "no keys known" (`None`) and "known to
have none" (empty) stay distinguishable — an edge set cannot express that
difference.

## The measurement

One tenant of 20 000 entities, warm, median of 15 runs:

| query | time | plan |
|---|---|---|
| `(:BlockingKey)<-[:BLOCKED_BY]-(:Entity)` | **4.18 ms** | `NodeUniqueIndexSeek` + `Expand(All)` |
| `$key IN e.blocking_keys` with the tenant seek | **19.89 ms** | `NodeUniqueIndexSeek` + `Filter` |

4.8x at this size, and — the part that matters — **the shapes differ, not the
constants.** The node form seeks one key node and expands its edges; the
property form seeks the tenant and filters every entity in it. The gap grows
linearly with tenant size.

Write cost, 500 entities carrying three keys each, alternated against 500
carrying none to cancel drift, median of 8:

| batch | time |
|---|---|
| 500 entities × 3 keys | **100.1 ms** |
| 500 entities, no keys | **40.8 ms** |

**2.45x, and it is two extra statements rather than two per entity.** The
second is skipped entirely when nothing in the batch carries keys. That
bounding is tested at two batch sizes, because a fixed query count asserted at
one size cannot tell a bounded implementation from a per-entity loop.

> **Provenance.** These numbers were measured in slice 7 against the Neo4j 5
> container in `docker-compose.test.yml`. The benchmark was a throwaway script
> and is not in the tree; the *plan* assertions it produced are, in
> `tests/integration/graph/test_neo4j_store.py`, and those run under
> `-m integration`. Nothing in the default gate re-measures the timings. If
> you need to trust them again, re-measure.

## The trap this decision creates

Nodes mean a **second write path**. An entity's `:BLOCKED_BY` edges must be
*rebuilt* on every upsert, not merely added to — otherwise a re-upsert that
drops a key leaves the old edge behind and `find_by_blocking_key` keeps
returning an entity that no longer carries that key. Stale keys make
consolidation propose merges from evidence that has been withdrawn.

`_write_blocking_keys` deletes before it merges, and
`test_find_by_blocking_key_reflects_the_latest_write` is the test that fails if
that is ever reordered. Orphaned `:BlockingKey` nodes are cleaned up because an
orphan matches nothing and leaving it would be a slow leak.

**This is why the deferral through slices 4-6 was right on correctness grounds
and not merely on scheduling grounds.** Adding the node model in slice 4, when
nothing read blocking keys, would have added an untested second write path.

## A methodological note worth more than the result

**The first measurement was wrong and reported `NodeIndexScan`.** Neo4j will
not plan against an index that is not yet `ONLINE`, and index population is
asynchronous — the benchmark had created the schema moments earlier.
`CALL db.awaitIndexes()` before measuring changed the answer from a scan to a
seek. Any plan assertion taken without it is measuring the population race.

## Consequences

- The port did not change. This is entirely an adapter concern plus one
  constraint in `_SCHEMA`.
- `InMemoryGraphStore` is unaffected — a dict of sets was always membership-
  shaped — which is why the compliance suite could not have found this. A
  cost difference that only one adapter has is invisible to a shared
  behavioural suite, and that is the general lesson: assert the query plan, not
  only the results.
