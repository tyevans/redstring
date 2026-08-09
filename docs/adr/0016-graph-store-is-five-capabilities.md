# ADR 0016: `GraphStore` is five capability protocols, composed

**Status:** accepted. Applied to `ChunkStore` and `Cache` by
[ADR 0026](0026-chunk-store-and-cache-are-capabilities-too.md), and to
`VectorStore` by
[ADR 0027](0027-vector-store-is-three-capabilities-and-so-is-every-collaborator.md)
— which also amends the judgement below that `CandidateFinder` is honestly
typed by the whole port. Extended by
[ADR 0028](0028-a-capability-declares-its-own-release.md), which gives every
capability here a `close` and an `async with` pair through `AsyncClosable`;
the five capabilities and their boundaries are unchanged.

**Why this is an ADR:** it changes the shape of a store port, which
`.claude/rules/definition-of-done.md` names explicitly, and it changes what a
collaborator's signature promises. It amends
[ADR 0002](0002-two-store-ports.md) rather than superseding it — there are
still exactly two store ports, and `GraphStore` is still the thing an adapter
implements.

## Context

`GraphStore` had eighteen methods on one Protocol. Every first-party
collaborator was typed against all eighteen, and measured, almost none of them
needed that:

| Collaborator | Methods it calls |
|---|---|
| `TemporalQuery` | 1 — `find_entities` |
| `ConsolidationService` | 1 — `get_relationships_for` |
| `CandidateFinder` | 3 |
| `GraphProjection` | 8 |

Three of four use three or fewer. That is the interface-segregation complaint
in its plainest form, and the argument for fixing it is not tidiness.

**The cost was paid, and recently.** Writing a test that needed a `GraphStore`
returning entities in a non-default order, the author faked it by
*subclassing `InMemoryGraphStore`* and overriding one method — not because
inheritance was right, but because implementing an eighteen-method Protocol
for a test that needs one was absurd. A double that subclasses the thing it
stands in for inherits every behaviour of that thing, which is precisely what
a double must not do; the test was one careless override away from asserting
the in-memory adapter's semantics rather than the code under test.

That is the failure ISP predicts: a fat interface does not merely inconvenience
implementers, it pushes them toward inheriting a real implementation, and the
resulting test proves less than it appears to.

## Decision

`GraphStore` becomes the composition of five capability protocols, each
`runtime_checkable`:

| Protocol | Holds | Why it is a seam |
|---|---|---|
| `EntityReader` | `get_entity`, `get_entities`, `find_entities`, `find_by_blocking_key`, `find_by_blocking_keys` | The narrowest useful slice, and the one most collaborators want |
| `EntityWriter` | `upsert_entity`, `upsert_entities` | Projections write, queries read, almost nothing does both |
| `AliasStore` | `upsert_alias`, `remove_alias`, `find_aliases`, `resolve_entity_ids` | The merge-consultation surface ADR 0002 argues for |
| `RelationshipStore` | six edge methods | Reads and writes stay together: a merge reads the edges it is about to redirect |
| `TenantPurge` | `delete_by_tenant` | Alone, because "this collaborator can wipe a tenant" should be visible in a signature |

Relationships are deliberately **not** split into reader and writer, unlike
entities. The split is by *who calls what*, not by symmetry: nothing in this
tree reads edges without also writing them.

Collaborators are narrowed to the capability they use. `TemporalQuery` takes
an `EntityReader`; `ConsolidationService` takes a `RelationshipStore`.
`CandidateFinder` and `GraphProjection` keep `GraphStore` — the first spans
three capabilities and the second uses eight methods, so for them the whole
port *is* the honest annotation.

## Consequences

**Nothing changes for an adapter.** `GraphStore` still names every method
through its bases, `runtime_checkable` still works, and
`tests/unit/graph/test_compliance_coverage.py` still finds all eighteen read
methods because `inspect.getmembers` walks the MRO. The compliance suites, the
two shipped adapters and the 1837-test suite were all green on the split with
no edit to any of them.

**The payoff is demonstrated rather than asserted.** The subclassing double
above is rewritten as `_ReversingEntityReader`, a genuine implementation of
`EntityReader`: one real method and four that raise `NotImplementedError` with
a reason. It still kills the mutant it was written for (deleting the
`_chronologically` id tie-break), and it can no longer silently answer a
question the timeline was not supposed to ask.

**Five names join the public surface**, pulled in by ADR 0006's closure gate
the moment a narrowed signature named one. That is a real cost and the right
one: it tells an implementer what each collaborator needs, which is the whole
point.

**The temptation to over-segregate is real and was declined once already.**
`CandidateFinder` spans three capabilities, and there is a version of this
decision where it gets a bespoke three-method protocol of its own. That is the
consumer-owned-interface form of DIP and it is defensible, but it multiplies
protocols per caller rather than per capability, and the port stops describing
the domain and starts describing its callers. If a sixth capability appears,
it should be because the *store* grew one, not because a caller wanted a
smaller argument.

## Alternatives rejected

**Leave it at eighteen and rely on discipline.** The subclassing double is
what discipline produced.

**Split reader/writer across the board.** Symmetric and larger; it would give
`RelationshipReader`/`RelationshipWriter` that nothing would ever request
separately, and an unused seam is a maintenance cost with no reader.

**Consumer-owned protocols everywhere.** The most orthodox DIP answer, and it
scales with the number of callers rather than the number of capabilities.
Revisit if a caller ever needs a slice these five cannot express.
