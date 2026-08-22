# ADR 0045: A lexical-only retriever is a constructor, not an omitted argument

## Status

Accepted.

Relates to [ADR 0017](0017-the-embedding-provider-port.md), which **stands** —
its rule that a configuration mistake surfaces at the seam is the reason this
ADR takes the shape it does, rather than the smaller one.
Relates to [ADR 0022](0022-the-lexical-channel-is-not-bm25.md), which
**stands**: the lexical channel over entities is still blocking-key recall
plus a string metric, and this ADR is about who has to be wired for it, not
about what it computes.
Relates to [ADR 0037](0037-one-exception-for-a-dimension-mismatch.md), which
**stands** — the dimension check remains at construction for the pair that has
one. Closes BACKLOG B163.

## Context

`Retriever.__init__` took `embeddings` and `vectors`, checked their dimensions
against each other, and stored both, before any mode had been chosen.
`retrieve` then branched per mode, and `RetrievalMode.LEXICAL` reached neither:
it is `find_by_blocking_keys` plus `lexical_score` over the entity's name, with
no vector anywhere in the path.

So a caller who wanted only the blocking-key channel still had to supply, wire
and keep healthy an embedding endpoint it never called. `mode=` could not help,
because both objects were already required by the time a mode could be named.

**This was found by a consumer, not by inspection.** `research-team` adopted
`Retriever` for its entity-search tool and chose `LEXICAL` deliberately —
fusing the semantic channel in made the tool answer name lookups with entities
matching the query nowhere in their text. Its embedding probe latches
"absent" when the endpoint is missing or misconfigured, a deliberate
degradation for consolidation scoring. The result was that switching embeddings
off, or mistyping a model name, silently removed *misspelling-tolerant entity
search* — a feature with no embedding in it — and left a plain substring scan
behind.

The local workaround was closed off, which is why this needed a decision here
rather than in the consumer. Assembling a lexical-only search the way a caller
can for chunks — `tokenize` and `rank_chunks` are exported, so `ChunkRetriever`'s
lexical half is reproducible without an `EmbeddingProvider` — is not possible
for entities: `query_blocking_keys`, `blocking_keys_for` and `lexical_score`
all live in `redstring.domain.blocking` and none is exported from the package
root.

## Decision

**`Retriever.lexical_only(graph=...)` and `ChunkRetriever.lexical_only(chunks=...)`
construct a retriever with no embedding provider.** The primary constructors
are unchanged: they still require every collaborator, and still refuse a
mismatched dimension pair at construction.

A lexical-only retriever carries `LEXICAL` as its **own default mode**, so
`mode` on `retrieve` and `retrieve_chunks` becomes `RetrievalMode | None`,
resolved per instance. Asking a lexical-only retriever for `SEMANTIC` or
`HYBRID` raises `ValueError`, naming the constructor that serves those modes.

### Why a constructor rather than optional arguments

The smaller change was to make `embeddings` and `vectors` optional on
`__init__` and raise from `retrieve` when a mode needing them was asked for.
It was rejected because it makes two different situations the same call:
"I want lexical only" and "I forgot to pass the provider" both become an
omitted argument. A caller in the second situation gets no error at the seam
and a `ValueError` at the first semantic query — in production, that is at the
first query of a shape the smoke test did not have. This is precisely what
ADR 0017's construction-time dimension check exists to prevent, and it would
have been odd to keep that check while removing the requirement it guards.

A caller naming `lexical_only` has said what it wants. A caller omitting an
argument has not said anything.

### Why the export option was rejected

The other shape on the table was to export `query_blocking_keys`,
`blocking_keys_for` and `lexical_score`, so a caller could assemble a
lexical-only entity search the way it already can for chunks. It removes the
asymmetry rather than working around it, and it was still declined: the
blocking scheme is what consolidation blocks on, and pinning it as public API
makes changing it a breaking change for reasons unrelated to retrieval. The
asymmetry it would have fixed is fixed anyway by giving `ChunkRetriever` the
same constructor — the two paths are symmetric again, at the level of the
composed class rather than at the level of the parts.

### Why `HYBRID` is refused rather than degraded

`HYBRID` has a lexical half that would answer. A retriever that simply skipped
the channel it could not run would return plausible results for a query the
caller believed was fused, which is the same silent loss of capability that
caused this ADR to be written. `SEMANTIC` would fail loudly either way;
`HYBRID` is the mode that had to be decided.

Note what this does **not** change on the chunk side. A `HYBRID` query over a
corpus whose rows carry no embedding still answers lexically and still does not
raise. "Unembedded" is a per-row fact on `StoredChunk`, so refusing it would
mean refusing some rows mid-answer — ADR 0038's consequence, and it stands. A
retriever built with no provider at all is the different case: a configuration
the caller stated, refusable at the point the caller names the mode.

## Consequences

- A deployment that runs entity search without embeddings can now express
  that, and gets an error at construction if it later asks for a mode it did
  not wire for.
- `mode` is now `RetrievalMode | None` on both retrieve methods. Every existing
  call site is unaffected: `None` resolves to `HYBRID` for a retriever built
  the primary way.
- `lexical_only` bypasses `__init__`, so guards shared by both paths live in a
  `_wire` helper rather than in `__init__`. A guard added to `__init__` alone
  would hold for one construction path and not the other; only the dimension
  check stays there, because it has nothing to check when there is no provider.
- The public surface gains no new type. `EntityReader`, `ChunkStore` and
  `RetrievalMode` are already exported.
- The blocking helpers stay internal, so the blocking scheme remains free to
  change.
