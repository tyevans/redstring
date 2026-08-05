# ADR 0018: Batch relationship writes are atomic

## Status

Accepted. Amends the consequences of
[`0009` the extraction fold resolves through aliases](0009-the-extraction-fold-resolves-through-aliases.md),
which recorded the previous, weaker promise as load-bearing.
[`0002` two store ports](0002-two-store-ports.md) stands.

## Context

`GraphStore.upsert_relationships` used to promise the weakest thing it could:
a `MissingEntityError` part-way through **leaves earlier elements written**.
The argument was that a caller replaying an event log converges on the same
final state anyway, because every element is individually idempotent, so
defining the failure state bought nothing that idempotency did not already
provide.

That argument is sound as far as it goes. What it missed is that the weak
promise was not what the adapters did.

The Neo4j adapter validates every endpoint in one query before it writes
anything — not out of any commitment to atomicity, but because round-tripping
per element would be the expensive way to write it. So it wrote nothing on a
dangling edge. The in-memory adapter looped over `upsert_relationship` and
wrote the prefix. Two adapters of one port, differing on the state a caller
observes after an error, with the port blessing only one of them.

Nothing failed. The compliance suite asserted that `MissingEntityError` was
raised and never what survived it, so the axis was untested in both directions
at once. This is the recurring shape rather than an oversight: a promise
nothing asserts is not a promise, and the adapter written second inherits
whatever the author assumed.

## Decision

**`upsert_relationships` is atomic.** Either every element is written or none
is, and a failure leaves the store exactly as it was before the call.

Atomicity is scoped to the call. A failed batch does not disturb what an
earlier call wrote — rolling back further would be a worse bug than the one
being fixed, and it is asserted separately for that reason.

The in-memory adapter validates every endpoint before writing any element.
There is no rollback and none is needed: nothing is written until everything
is known good.

## Consequences

**The stronger contract cost one adapter two passes and the other nothing.**
That asymmetry is why this direction was chosen over pinning the weak version:
the adapter that would find atomicity expensive already had it, so "pin the
weak contract" would have meant writing a test to permit a behaviour neither
adapter exhibited and no caller wanted.

**A caller may now rely on the store being unchanged after the error.** The
previous guidance — retry the whole batch, never skip an assumed-written
prefix — remains correct, and is now correct for a simpler reason.

**ADR 0009's fold argument is unaffected and slightly strengthened.** It
leaned on per-element idempotency so that a retry converges without a rollback
the `GraphStore` interface does not offer. That is still true; atomicity means
there is less to converge from. The partial application 0009 and the rebuild
how-to describe — entities written, then the relationship call refused — is a
property of the *fold* making two calls, not of one batch, and is unchanged.

**A future adapter over a store without multi-row transactions has to do the
validation pass itself.** This is a real cost and the honest one to name: the
port now requires something a backend may not give for free. It is bounded —
validate, then write — and the alternative was two adapters that already
disagreed.
