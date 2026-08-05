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
that share one — see
[Consolidate duplicate entities](../how-to/consolidate-duplicate-entities.md).
`GraphStore` exposes the lookup twice, as `find_by_blocking_key(key, tenant)`
and the batched `find_by_blocking_keys(keys, tenant)`, and
`CandidateFinder._block` calls the batched form **once per subject entity**.
One call per key or one call per subject, the cost that matters is the same:
this lookup runs a number of times proportional to the size of the tenant.

`Entity.blocking_keys` is a `frozenset[str] | None`, and the natural Neo4j
representation is a list property on `:Entity` — which is what
[the Neo4j graph store reference](../reference/neo4j-graph-store.md) still
documents alongside the key nodes.

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

## The trap this decision creates: edges must be rebuilt, not added to

Nodes mean a **second write path**. An entity's `:BLOCKED_BY` edges must be
*rebuilt* on every upsert, not merely added to — otherwise a re-upsert that
drops a key leaves the old edge behind and `find_by_blocking_key` keeps
returning an entity that no longer carries that key. Stale keys make
consolidation propose merges from evidence that has been withdrawn, which is
the failure [Consolidate duplicate entities](../how-to/consolidate-duplicate-entities.md)
is least able to notice: a candidate pair that shares a withdrawn key looks
exactly like one that shares a live key.

`_write_blocking_keys` is written delete-then-merge for this reason: one
statement deletes every existing `[old:BLOCKED_BY]` edge for the rows in the
batch, a second `MERGE`s the key nodes and the new edges. Every row goes
through the delete, including rows whose `blocking_keys` is null — an entity
going from "has keys" to "has none" must lose its edges just as much as one
going from one key to another, and that is the case a create-only
implementation gets wrong.

`test_find_by_blocking_key_reflects_the_latest_write` in
`tests/compliance/graph_store.py` is what holds the ordering honest, **and it
proves exactly one thing**: after re-upserting an entity whose only key
changed from `"old"` to `"new"`, `find_by_blocking_key("old", tenant)` returns
`[]` and `"new"` returns that entity. That is the read-visible half of the
trap — an entity that dropped a key stops being returned — and it is the half
that matters for correctness.

It says nothing about what the graph now *contains*, and it structurally
cannot. It lives in the compliance suite, which is
**adapter-agnostic by construction**: the same body runs against
`InMemoryGraphStore`, where blocking keys are a dict of sets and there are no
key nodes at all. A shared suite can only state claims both adapters can
satisfy, so it can assert what a lookup returns and never assert anything
about Neo4j's node-level layout — not that `:BlockingKey` nodes exist, not how
many there are, not whether any is left childless. See
[ADR 0002](0002-two-store-ports.md) for why the port is defined by that shared
behavioural contract in the first place. Claims about storage layout need an
integration test against the container
([Run the integration and mutation suites](../how-to/run-integration-and-mutation-suites.md)),
and the next section is about one such claim that has none.

## Orphaned `:BlockingKey` nodes are NOT reaped on upsert

**A re-upsert that drops a key leaves the key node behind.** This is stated
here because an earlier revision of this ADR claimed the opposite — that
orphans are cleaned up because leaving them would be a slow leak — and the
code has never done that.

What `_write_blocking_keys` actually does is two statements and no third: the
first deletes every existing `[old:BLOCKED_BY]` edge for the rows in the
batch, the second `MERGE`s the key nodes and the new edges. Nothing looks at
a key node the first statement has just left with no incoming edge. The only
place in the adapter where a `:BlockingKey` node is ever removed is
`delete_by_tenant` (`graph/adapters/neo4j.py:659-666`):

```cypher
MATCH (k:BlockingKey {tenant_id: $tenant_id}) DETACH DELETE k
```

That statement exists precisely because `DETACH DELETE` on the tenant's
entities takes the `:BLOCKED_BY` edges but not the key nodes — so even the
wipe path had to reap them explicitly. It is **whole-tenant**: it is a reset,
not a garbage collection, and it is unreachable from the upsert path.

The consequence is bounded in one direction and unbounded in the other. A
childless key node **matches nothing** — `find_by_blocking_key` seeks the key
node and expands its `:BLOCKED_BY` edges, and an orphan has none — so no read
returns a wrong answer and consolidation sees no withdrawn evidence. This is
not the stale-edge failure of the previous section; correctness is intact.
What is not bounded is the count. Under the uniqueness constraint on
`(tenant_id, key)` there is exactly one node per distinct key ever written,
and a tenant that churns keys — which is what re-extracting a changed document
does — accumulates one node per key ever seen, cleared only by a full tenant
wipe.

The fix is not free, which is why it is deferred rather than applied: a
`WHERE NOT EXISTS { (k)<-[:BLOCKED_BY]-() } DELETE k` pass would be a third
statement on every batch upsert, against a write path this ADR measured at
2.45x and defended on the grounds that it is *two* extra statements rather
than two per entity. See **B62** in [`BACKLOG.md`](https://github.com/tyevans/redstring/blob/main/BACKLOG.md) for the
open item and the reasoning; the leak's size is unmeasured, and the decision
was to measure it before paying for it.

## Why no test can currently see this, and what one would have to assert

Nothing in the suite observes the residue, and it is worth being explicit
about why, because the absence looks like an oversight and is not one.

`InMemoryGraphStore` keeps no key index at all: `find_by_blocking_key` walks
the tenant's entities and tests `key in entity.blocking_keys`
(`graph/adapters/memory.py:104-118`). A key exists there only as a member of
some entity's `frozenset`, so a key nothing carries has already ceased to
exist — there is no separate object to orphan, and the concept does not exist
on that side of the port. And under **both** adapters the read is identical:
`find_by_blocking_key` on an orphaned key returns `[]`. The compliance suite,
being adapter-agnostic (previous section), can only assert things both
adapters can satisfy, and both satisfy "returns nothing" whether or not a node
is left lying in the store.

Observing the leak therefore needs an integration test against the container,
in `tests/integration/graph/test_neo4j_store.py`, run under `-m integration`
([Run the integration and mutation suites](../how-to/run-integration-and-mutation-suites.md)).
It would re-upsert an entity with a key dropped from its `blocking_keys`, then
count `(:BlockingKey)` nodes for the tenant with no incoming `:BLOCKED_BY`
edge, and assert on that count — an assertion no behavioural read can stand in
for, since the value the port returns is the same either way.

Until such a test exists, **treat this section as unverified**: it is grounded
in a reading of `_write_blocking_keys` and `delete_by_tenant`, not in a
failing or passing test. Cite B62, not a test, when the question comes up.

## Why the deferral through slices 4-6 was right

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
- The write path pays for it: two extra statements per batch, measured at
  2.45x on 500 entities carrying three keys, and skipped entirely when no row
  in the batch carries keys. That bounding is the property to protect —
  assert it at two batch sizes, or a per-entity loop passes.
- `InMemoryGraphStore` is unaffected — its lookup already scans the tenant's
  entities and tests membership of a `frozenset` — which is why the
  compliance suite could not have found this. A cost difference that only one
  adapter has is invisible to a shared behavioural suite, and that is the
  general lesson: assert the query plan, not only the results.
- **That one-adapter-only lesson now cuts twice.** The same adapter-agnostic
  suite that could not see the *cost* difference cannot see *node-level
  residue* either: orphaned `:BlockingKey` nodes exist only under Neo4j, and
  both adapters return `[]` for them, so no shared assertion can distinguish
  a store that reaps them from one that does not. Cost and layout are two
  faces of the same blind spot — the compliance suite constrains what a read
  returns and nothing else. So: **every claim in this ADR about Neo4j-only
  storage layout must cite an integration test in
  `tests/integration/graph/test_neo4j_store.py`
  ([Run the integration and mutation suites](../how-to/run-integration-and-mutation-suites.md))
  or be labelled unverified.** The plan assertions meet that bar; the orphan
  claim does not, and is labelled and carried as **B62** in
  [`BACKLOG.md`](https://github.com/tyevans/redstring/blob/main/BACKLOG.md). An earlier revision of this ADR asserted
  cleanup that the adapter never performed and stood for three slices —
  precisely because no test could contradict it, and nobody had written down
  that no test could.
