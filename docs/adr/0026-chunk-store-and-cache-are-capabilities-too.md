# ADR 0026: `ChunkStore` and `Cache` are composed from capabilities, like `GraphStore`

## Status

Accepted, and **amended by
[`0027`](0027-vector-store-is-three-capabilities-and-so-is-every-collaborator.md)**,
which corrects the claim below that `ChunkStore` and `Cache` were the two
*remaining* ports: `VectorStore` had the same problem and is named in neither
this ADR nor 0016, and **extended by
[`0028`](0028-a-capability-declares-its-own-release.md)**, which moves this
ADR's `close` -- and the `async with` pair the ports had never declared -- onto
every capability through a shared `AsyncClosable` base, by the same `mypy`
arbitration recorded below. Applies [`0016` `GraphStore` is five
capabilities](0016-graph-store-is-five-capabilities.md) to two of the three
ports that had the problem. Amends [`0008` the two non-store
ports](0008-the-two-non-store-ports.md) and [`0023` the chunk
corpus](0023-the-chunk-corpus.md) in their typing only: no method is added,
removed or respecified, and every adapter in the tree is untouched.

## Context

0016 argued that depending on eighteen methods in order to call three is the
interface-segregation complaint in its plainest form, and split `GraphStore`
accordingly. The argument was made about one port. It applies unchanged to two
others, and in one case the numbers are worse.

**`ChunkStore` is nine methods with one first-party consumer.**
`ChunkProjection` calls `replace_source`. That is the entire first-party use.
The other eight — `get`, `get_by_source`, `get_by_entity`,
`lexical_candidates`, `upsert_many`, `delete_by_source`, `delete_by_tenant` —
exist for library users, which is an excellent reason for the *port* to offer
them and no reason at all for the projection to depend on them. One of nine,
against 0016's three of eighteen.

The cost is the same currency 0016 measured: `tests/compliance/chunk_store.py`
is over a thousand lines, so an author writing a chunk store to serve only the
corpus-write path owed a read, rank and delete surface they would never call.

**`Cache` is eight methods whose two consumers partition it exactly.**
`llm/circuit_breaker.py` uses `get`, `set`, `increment`, `delete`;
`llm/rate_limiter.py` uses `record_hit`, `count_hits`, `oldest_hit`; both use
`close`. Neither touches the other's four. `ports/cache.py` has carried a
section heading reading **"## Two capabilities, not one"** since it was
written — the analysis was done, and then expressed as prose above a single
flat protocol. Someone implementing a `Cache` to get distributed circuit
breaking across workers owed a sliding-window hit log regardless.

Nobody has yet been caught by either gap, because every adapter in this tree
implements its whole port. That is `.claude/rules/recurring-defects.md` §3
rather than a reason to wait: a rule holding only because nobody has tested it
is indistinguishable from no rule.

## Decision

**Compose both ports from capability protocols, and export the capabilities.**

| Port | Capabilities |
|---|---|
| `ChunkStore` | `ChunkWriter`, `ChunkReader`, `LexicalCandidateSource`, `ChunkPurge` |
| `Cache` | `KeyValueCache`, `HitWindow` |

Adapters continue to implement the composed port and the compliance suites
continue to run against it. Collaborators are narrowed to the capability they
call: `ChunkProjection` is now a `StoreProjection[ChunkWriter]`,
`CircuitBreaker` holds a `KeyValueCache`, `RateLimiter` a `HitWindow`.

**`close` belongs to both cache halves rather than to a lifecycle protocol of
its own.** The first attempt gave it one, reasoning that neither consumer
called it; `mypy` refuted that immediately — both forward it. Releasing what an
adapter holds is a property of holding one, so it belongs to every capability
rather than beside them.

**`LexicalCandidateSource` is the capability most worth having separately**,
and it is the one that is not merely a narrowing. Ranking needs recall and
corpus statistics and nothing else — see
[`0024`](0024-bm25-over-the-chunk-corpus.md), which put the scorer in the
domain precisely so it depends on no store. A caller who can supply those from
an index that is not a chunk store at all can now say so in a type.

## Consequences

**Nothing changes for an adapter.** Each composed protocol still names every
method through its bases, `runtime_checkable` still answers structurally, and
`tests/unit/chunks/test_compliance_coverage.py` still finds every read method,
because `inspect.getmembers` and `typing.get_type_hints` both walk the MRO.
This was 0016's claim and it held again.

**The public surface grows by six names.** 0016's five capabilities are
exported, so consistency requires these to be. A caller cannot narrow an
annotation to a type they may not import.

**Each split is enforced by a test that would fail if it were reverted**, and
the shape of those tests is the part worth copying. Each builds a double
implementing *one capability and nothing of the other*, subclassing nothing —
a double built by subclassing the real adapter would satisfy the whole port
however the protocols were declared, and could not tell you the split held.
Each also asserts the real adapter still satisfies *every* capability, which
is what catches a split turning into a fork.

**A capability with no consumer is now visible as such.** `ChunkPurge` and
`ChunkReader` have no first-party caller at all. That was true before and
invisible; it is now written in the type system, and the next question — should
the library's own retrieval path be using them — is one somebody can now ask.
