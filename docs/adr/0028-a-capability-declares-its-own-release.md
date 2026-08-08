# ADR 0028: A capability declares its own release

## Status

Accepted. Extends the capability decomposition of
[`0016` `GraphStore` is five capabilities](0016-graph-store-is-five-capabilities.md),
[`0026` `ChunkStore` and `Cache` are capabilities
too](0026-chunk-store-and-cache-are-capabilities-too.md) and
[`0027` `VectorStore` is three
capabilities](0027-vector-store-is-three-capabilities-and-so-is-every-collaborator.md)
with a member each of those three left to the adapters. All three **stand**:
no capability is split, merged or renamed, no method changes shape, and the
lines those ADRs drew are untouched. [`0008` the two non-store
ports](0008-the-two-non-store-ports.md) **stands** and is deliberately *not*
extended — see the Decision. [`0002` two store ports](0002-two-store-ports.md)
is amended in its typing only: the ports gain a member every adapter behind
them already had.

## Context

Four adapters own a driver, a connection pool or a client, and each grew
`__aenter__`/`__aexit__` so a caller could not leak one by forgetting a
`finally`. The ports did not. The consequence is the one worth stating in a
decision record rather than a commit: **`async with` was reachable only by
naming the concrete adapter class.** A caller who had done everything this
library asks of them — depend on the port, let composition choose the
backend — could not write the safe form at all, while a caller who hard-coded
`Neo4jGraphStore` could. The abstraction was charging a correctness penalty
for being used.

`Cache` had half-answered the question in the permissive direction: it
declared `close()` and neither store port did, so the same adapter fleet
promised release through one port and not the others. That inconsistency is
what made this a decision rather than an omission — two answers were already
in the tree.

The third force is 0027's. Once collaborators are narrowed to capabilities, a
port-level member that lives only on the *composed* protocol is invisible to
every narrowed caller. A `StoreProjection[ChunkWriter]` holds the store as
completely as anything else does.

## Decision

**`AsyncClosable` is a protocol declaring `close`, `__aenter__` and
`__aexit__`, and every capability protocol inherits it.** Not a sibling
protocol adapters also satisfy, and not a member of the composed ports alone.

**`mypy` decided the shape, exactly as it decided `close`'s placement in
`Cache`.** 0026 records a lifecycle protocol standing *beside* the cache
halves being refuted within a minute. The same experiment was run again here
before choosing, because B107 had sketched the sibling form and it reads
well: a separate protocol that resource-owning adapters satisfy and in-memory
ones do not, checked structurally at a composition root. It fails for a
sharper reason than last time. A caller handed an `EntityReader` cannot narrow
back to a sibling `AsyncClosable` without a cast — Python has no intersection
type — so the sibling form is unreachable from precisely the position that
motivated the change. Inheritance makes the pair arrive through the MRO, which
is the mechanism every composed port here already runs on.

**Releasing what you hold is a property of holding it.** That is why this is a
base of each capability rather than a member of the composed port. It is 0026's
sentence about `close`, generalised once the block form asked the same
question, and 0027's `dimension` reasoning applied in the other direction: the
answer comes from what the methods say, not from a preference for the smallest
protocol. Every capability is a handle on one adapter; none of them is a
handle on less of it.

**An adapter that owns nothing writes the pair and says so.** The in-memory
adapters hold dictionaries the interpreter already owns, so "release what you
hold" is satisfied by doing nothing — and a no-op documented as a no-op is
honest, not apologetic. The alternative on offer was to keep those adapters
out of the promise, which is the same thing as not making the promise: a
caller cannot write one lifetime discipline against a port whose adapters
disagree about whether it has one.

**`LlmProvider` and `EmbeddingProvider` are excluded, and the exclusion is
asserted.** They are not store-shaped: their adapters hold an HTTP client
whose lifetime this library has never specified, and granting them the pair
before deciding what `close()` means there would put four no-op methods on an
adapter that genuinely does have something to release — the one case where the
no-op above stops being honest. Left unasserted, "not yet" and "deliberately
not" are the same state of the tree, which is
[`0014`](0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)'s
shape; so a test pins that those two ports are *not* closable, and extending
the decision to them means deleting it in the open.

## Consequences

**Nothing changes for a caller who already had a working lifetime.** The four
resource-owning adapters satisfied this the day it landed; the composed ports
name every member through their bases; `runtime_checkable` still answers
structurally.

**The public surface grows by one name.** A caller cannot narrow an annotation
to a type they may not import, which is 0006's closure gate — and the moment a
signature says `AsyncClosable`, it is the same obligation `VectorWriter`
created.

**The claim is enforced at the protocol, and the subject set differs from its
sibling module's on purpose.** The precedent gate derives its subjects from
classes assigning an `_owns_*` flag, because `close()` as a signal would catch
components owning nothing. That derivation is wrong here in the opposite
direction: an adapter owning nothing must still declare the pair, since the
port does. So the gate derives "every class satisfying a capability" instead,
structurally, and a fifth adapter is caught by being written rather than by
someone remembering the file. Both derivations are correct for their own
claim, and that is the reusable part — **a derived subject set follows from
what is being claimed, not from what is convenient to detect.**

**A no-op `close()` is a claim and is tested as one.** An in-memory store must
survive its own block, because "drop everything on close" is the available
over-implementation and it is what `MemoryCache` — correctly, for expiring
state — already does. The two behaviours now differ deliberately rather than
by whichever class the next reader happens to open.

**The question 0026 and 0027 each closed with has a third instance.** Both
noted a capability with no first-party caller. This one adds the reverse: the
two provider ports are now the only ports whose adapters have a lifetime the
library declines to describe, and that is a question with a name rather than a
silence.
