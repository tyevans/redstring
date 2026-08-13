# ADR 0037: One exception type for a dimension mismatch

## Status

Accepted.

Relates to [ADR 0012](0012-no-ann-index-in-a-multi-tenant-vector-store.md),
which **stands**. Relates to
[ADR 0017](0017-the-embedding-provider-port.md), which settles that
`EmbeddingProvider` declares its own dimension — that page **stands**; this
ADR is about what happens when two declared dimensions disagree, not about
the port itself.

## Context

Two composition entry points refuse an embedding provider and a vector store
whose dimensions disagree, and until now they refused it with two different
exception types. `Retriever.__init__` raised `DimensionMismatchError`.
`build_graph`'s internal wiring check raised a bare `ValueError`, for the same
condition. `DimensionMismatchError` extends `RedstringError`, which extends
`Exception` directly — it is not a `ValueError` subclass, so an `except`
written around either type does not catch the other.

Nothing asserted that the two entry points agreed, which is how the
divergence went unnoticed: each had its own test, and each test only checked
its own entry point's behaviour against itself. A caller wiring both `build_graph`
and `Retriever` behind one `try`/`except` around the configuration step
catches one mismatch and crashes on the other. The two checks are not
identical in shape — `build_graph` takes its provider and store as optional
and has a second failure mode, one collaborator supplied without the other,
that `Retriever` cannot have, since all three of its collaborators are
required. So "make them agree" was two separate questions: whether the
half-configured case and the mismatched-dimension case ought to share a type
at all, and which type the dimension case gets.

## Decision

**The exception type that names the condition wins.** A dimension mismatch
between an embedding provider and a vector store is now always
`DimensionMismatchError`, at every composition entry point that performs the
check. `build_graph` raises it in place of the `ValueError` it used to raise;
`Retriever.__init__` is unchanged.

**The half-configured case keeps `ValueError`.** A provider supplied without
a store, or the reverse, is a different mistake from a dimension
disagreement — there is no dimension to have disagreed on when one
collaborator is entirely absent — and it has no equivalent at any other entry
point to disagree with.

**The gate that would have caught this is introspective, not enumerated by
hand.** A test parametrised over a manually written list of entry points is
exactly the shape that let the original two diverge — each existing test
already covered its own entry point and none of them looked sideways. The
new test instead walks `redstring.composition`'s public surface for every
callable whose signature names an `EmbeddingProvider`, asserts that set
against the enumerated cases, and fails loudly when the two disagree. A
fourth entry point that takes an `EmbeddingProvider` is covered by
construction: the introspective half of the gate fails first, before its
case can be written wrong.

## Consequences

This is a **breaking change**. Any caller catching `ValueError` around a
`build_graph` call that mismatches embedding dimensions no longer catches it;
`DimensionMismatchError` must be caught instead. It ships with a version
bump, not a quiet patch release.

The half-configured case is deliberately excluded from this decision and
remains a `ValueError`. Widening `DimensionMismatchError` to cover it, or
introducing a shared base for both failure modes, is a separate decision this
ADR does not make.

A future composition entry point that accepts an `EmbeddingProvider` and a
store is covered by the introspective gate the moment it is added to
`redstring.composition`'s public surface; its own test case still has to be
written, but the gate fails until it is, rather than passing silently on a
stale enumeration.
