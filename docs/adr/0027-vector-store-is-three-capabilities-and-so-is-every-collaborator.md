# ADR 0027: `VectorStore` is three capabilities, and the collaborators are narrowed to match

## Status

Accepted. Finishes the sweep [`0016` `GraphStore` is five
capabilities](0016-graph-store-is-five-capabilities.md) started and
[`0026` `ChunkStore` and `Cache` are capabilities
too](0026-chunk-store-and-cache-are-capabilities-too.md) claimed to complete.
Amends [`0002` two store ports](0002-two-store-ports.md) in its typing only —
no method is added, removed or respecified, and no adapter is touched. Amends
0016's judgement about `CandidateFinder` and 0026's Consequences. **Extended
by [`0028`](0028-a-capability-declares-its-own-release.md)**, which adds the
lifecycle member these three capabilities -- and every other -- inherit; the
split below is untouched.

## Context

0026 opens by saying it applies 0016's argument "to the two remaining ports
that had the same problem", and names `ChunkStore` and `Cache`. `VectorStore`
appears in neither ADR, and it had the same problem the whole time. The claim
was not wrong about the ports it examined; it was wrong that they were the
remaining ones.

**A port left flat, and a projection left wide.** `VectorProjection` calls
`upsert_many` and nothing else. Its sibling `ChunkProjection` — same package,
same job, folding one event into one store — was narrowed to `ChunkWriter` in
0026 on exactly that reasoning, while `VectorProjection` kept the whole port.
Two projections written to the same shape, given different answers, one commit
apart.

**A rule stated in a port and not applied to two of its own collaborators.**
`ports/graph_store.py` says, as a claim about this codebase, that collaborators
should not depend on eighteen methods to call three and should "narrow the
annotation to the capability actually used". `Retriever` was typed against
`GraphStore` and `VectorStore` and reads through both. `CandidateFinder` was
typed against `GraphStore` under a docstring reading "Blocks and scores. Never
writes, never decides".

That last one is the finding worth stating plainly rather than counting
methods over. `TenantPurge` is alone in a protocol, and its own docstring says
why: to make "this collaborator can wipe a tenant" a visible fact about a
signature. A capability whose entire value is in being *withheld* stops having
any value the moment it is granted by default. It was granted to two
collaborators that never purge anything and to one whose stated contract is
that it never writes at all.

## Decision

**Compose `VectorStore` from three capability protocols**, and narrow every
first-party collaborator to what it calls.

| Capability | Holds |
|---|---|
| `VectorWriter` | `dimension`, `upsert`, `upsert_many` |
| `VectorReader` | `dimension`, `get`, `search` |
| `VectorPurge` | `delete`, `delete_by_tenant` |

Every first-party consumer is covered exactly by one of the three, which is
what decided where the lines fell — the split is by *who calls what*, the same
rule 0016 used to keep `RelationshipStore` whole rather than splitting it for
symmetry. `get` and `search` stay together because `CandidateFinder` reads the
subject's vector and then asks what is near it, and neither half serves it
alone.

**`dimension` belongs to writing and to reading, not to purging.** 0026 records
`close` ending up in *both* cache halves after `mypy` refuted a lifecycle
protocol of its own, and the same question was asked here with the opposite
starting guess. The answer came from the port rather than from a preference:
`upsert`, `upsert_many` and `search` are the three methods that accept or
return a vector, and they are exactly the three whose contract says
`DimensionMismatchError`. `VectorPurge` names ids only, so a caller who can
only delete has no vector length to agree about.

**A `VectorSearcher` holding `search` alone was tried and dropped.** The
attraction was symmetry with `ChunkStore`'s `LexicalCandidateSource`, which
0026 calls the capability most worth having separately. It does not carry over:
`lexical_candidates` is separable because BM25 ranking genuinely needs recall
and statistics from any index at all, while `search` here has no caller that
does not also reach for `get` or `dimension` in the same breath. A capability
nobody can request is `.claude/rules/recurring-defects.md` §3 wearing a
Protocol.

**`CandidateFinder` takes a `ConsolidationGraph`** — `EntityReader`,
`AliasStore` and `RelationshipStore`, composed. 0016 left it on the whole port,
reasoning that a collaborator spanning three capabilities is honestly typed by
the composed one; three of five is not five, and the two it does not span are
`EntityWriter` and `TenantPurge`. 0016 also declined a bespoke three-method
protocol here and said to revisit "if a caller ever needs a slice these five
cannot express". This is that revisit, and the *form* is what keeps it inside
0016's reasoning: naming a caller's combination of existing capabilities adds
no method and regroups nothing, so the port still describes the store. Inventing
a three-method interface would have started describing its callers.

**`Retriever` takes an `EntityReader` and a `VectorReader`.** It reads, and
that is now all its signature claims.

## Consequences

**Nothing changes for an adapter.** `VectorStore` still names every method
through its bases, `runtime_checkable` still answers structurally, and
`tests/unit/vector/test_compliance_coverage.py` still derives `get` and
`search` by introspection, because `inspect.getmembers` and
`typing.get_type_hints` both walk the MRO. This was 0016's claim, it held for
0026, and it held again.

**The public surface grows.** The three vector capabilities plus
`ConsolidationGraph`. A caller cannot narrow an annotation to a type they may
not import, so this follows from 0006's closure gate the moment a narrowed
signature names one.

**`ConsolidationGraph` lives beside its consumer rather than in `ports/`,
and that placement is provisional.** It is a composition of capabilities the
port declares, so `ports/graph_store.py` is arguably its home; it is also a
statement about one caller, which is what `consolidation/protocols.py` already
holds for `CandidateSource` and `MergeAdjudicator`. It sits in
`consolidation/candidates.py` today. Moving it is a rename with no behavioural
component, and the argument for either home is worth having once rather than
inferring from where it happens to be.

**Amendment: that argument was had, and it settled on
`consolidation/protocols.py`.** The deciding fact is not the layering — unlike
`CandidateSource` and `MergeAdjudicator`, `ConsolidationGraph` names only
types `ports/` already declares, so it *could* sit there and compile. It is
what the port would become. A port module describes the store; a composition
describes one consumer's subset of it, and a `ports/` module carrying those
accumulates one protocol per caller, which is what 0016 rejected under
"consumer-owned protocols everywhere". Declaring it beside the consumer that
shaped it keeps 0016's line — the port still describes the store — while
keeping the room 0016 left for "a slice these five cannot express".

`consolidation/protocols.py` therefore holds two kinds of thing, and its
docstring now says so: two substitution points a caller may replace, and one
narrowing that nobody substitutes. What they share is the direction they face
— each states what consolidation needs rather than what a store offers.

The public name is unchanged: `redstring.ConsolidationGraph`, exported from
`__all__` exactly as before. Nothing about the type, its bases or its
`runtime_checkable` behaviour moved with it.

**The narrowing is enforced by asserting the declarations, because nothing
else can.** This is the part of the sweep that had not been checked before and
is worth recording. The precedent tests from 0026 build a double implementing
one capability and drive a real consumer with it, which is the right shape and
is *behaviourally* silent: widening every annotation in this ADR back to the
composed ports leaves those tests green and the configured `mypy` run silent,
because the gate covers `src/redstring` and not `tests/`. Measured, not
assumed. So each new test module carries a class that reads the annotation
itself — the projection's generic argument through `__orig_bases__`, the two
constructors' raw annotation strings. They cannot tell you a caller passes
something too narrow; they are what makes a reverted narrowing fail rather
than merely read differently.

**0026's closing observation now has a second instance.** It noted that
`ChunkPurge` and `ChunkReader` have no first-party caller, and that the type
system had made a previously invisible fact askable. `VectorPurge` is the same:
nothing in this library deletes a vector or wipes a tenant's embeddings, and
`VectorProjection._truncate_read_models` raises rather than doing it. Whether
the library's own replay path should be able to is now a question with a name.
