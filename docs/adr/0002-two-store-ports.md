# ADR 0002: Two store ports, and the absence of `delete_entity`

**Status:** accepted, slices 3-5 of the ring migration.

**Why this is an ADR:** a port is the one interface that is expensive to
change, because every adapter and the compliance suite move with it. Three
adapters implement `GraphStore` today. The absent method in particular keeps
being proposed for symmetry, and the argument against it is not obvious from
reading the Protocol.

## Context

Before the migration, persistence was SQLAlchemy models plus a service layer
that reached into them, and "the graph" was a 443-line Neo4j client with no
callers. Storage decisions and domain decisions were the same code.

## Decision: two ports, `GraphStore` and `VectorStore`, not one

They are separated because their contracts differ in kind, not in degree.
`GraphStore` is exact: a write is visible to the next read, filters are
predicates, and results are sets. `VectorStore` is approximate by nature —
`search` returns the `k` nearest, and "nearest" is a property of an index that
a real backend is entitled to approximate.

Merging them would force one compliance suite to state both contracts, and the
weaker one wins: an exactness assertion that some adapters may fail is not a
contract. Keeping them apart let `tests/compliance/vector_store.py` grow a
separate *recall* tier, which states the weaker property honestly.

**A capability flag was explicitly rejected.** An `is_approximate` on the port
would let each adapter opt out of whichever assertions it fails, which is how
two adapters quietly stop being interchangeable while both "pass the suite".

## Decision: there is no `delete_entity`, and there will not be one

This is the part that gets re-proposed. The reasoning, in the order it was
found:

1. **Nothing needs it.** The obvious caller is a merge — but a merge does not
   delete the absorbed entity. It records an `Alias`, and the store keeps that
   alias.

2. **Deleting would break replay.** `DocumentExtracted` folded after
   `EntitiesMerged` writes the pre-merge endpoints back and silently reverts
   the merge — in strict log order, with every event delivered exactly once, no
   race required. The fix is that the extraction fold resolves each endpoint
   through the alias table before writing, which requires the alias to still be
   there. This was `BACKLOG` B34, and it is pinned in
   `tests/unit/projections/test_known_gaps.py`.

3. **A delete-then-insert projection is not idempotent.** Making the fold
   delete a document's entities before reinserting them would make redelivery
   destructive: the delete half of a redelivered event removes entities a later
   event added. At-least-once is the normal bus guarantee.

`delete_by_tenant` covers bulk removal, which is what replay and test teardown
actually want. The consequence is recorded honestly rather than hidden: a
re-extraction that finds *fewer* entities than the previous run leaves the
dropped ones in the graph forever, so the graph converges on the union of every
run rather than on the latest one. That is `BACKLOG` B32, still open.

## Decision: the compliance suite is the contract

`tests/compliance/` is not a convenience. It is the only artifact that says
what a port means, and the migration produced repeated evidence that the
Protocol alone does not:

- Slice 5 found two adapter divergences in validation paths — pgvector
  deduplicating before validating where in-memory validated everything, and
  in-memory raising `TypeError` on an unhashable stored `entity_type` where
  pgvector returned `[]`. Both were invisible to the suite as written, for
  input-shape reasons; both are now covered.
- Slice 3 injected 28 deliberate defects into the in-memory adapter one at a
  time. Two escaped the suite, and both escapes were port gaps rather than test
  gaps: `neighbors` deduplicates by entity, so relationship state — type,
  confidence, properties — was unobservable through the port entirely. That is
  why `get_relationships` exists.

Two structural properties of the suite are load-bearing and easy to undo by
accident:

- **Adapters supply `new_store()` rather than overriding a `store` fixture.** A
  pytest fixture is per-test-function, not per-hypothesis-example, so a shared
  store leaks state across examples and silently weakens every property test.
- **Every read method needs a mutation-isolation test.** Four read methods
  shipped without one, and each time a mutation run — not review, not the
  property tests — found that a shallow copy passed everything. This is now
  enforced by introspection over the Protocol in
  `tests/unit/graph/test_compliance_coverage.py`. `CLAUDE.md` explains why a
  written rule was not enough: it had already failed four times.

## Consequences

- Adding a port method means the compliance suite, its mutation-isolation test,
  its tenant-isolation test, and every adapter — deliberately expensive.
- `VectorStore`'s recall tier currently passes trivially, because both adapters
  are exact. Whoever adds an approximate adapter must strengthen it first
  (`BACKLOG` B10k).
- Hop distance was left off `neighbors` knowingly (`BACKLOG` B10c1), with the
  retrofit costed in both adapters at the time the decision was taken.
