# ADR 0017: The embedding provider is a port, and it declares its dimension

## Status

Accepted. **Amended by**
[`0043` a query is embedded differently from a document](0043-a-query-is-embedded-differently-from-a-document.md),
which adds `embed_query` to the port and a pair of task prefixes to both
adapters. The decision below stands as written: the port is still narrow, the
dimension is still declared on both sides, and the batch contract is unchanged
-- `embed_query` has it too.

**What 0043 extends is the identity argument in "(2) is tempting and wrong
here" below.** That paragraph reads "changing embedding model means a new
store, not an in-place change". The *task prefix is part of the model's
identity* for that purpose: one model at one dimension, with and without
`search_document: `, produces vectors that are not comparable. Read the
sentence as "changing embedding model **or its document prefix** means a new
store".

Relates to [ADR 0002](0002-two-store-ports.md), which settles that `GraphStore`
and `VectorStore` are the store ports — it **stands**; this adds a non-store
port, not a third store. Relates to
[ADR 0008](0008-the-two-non-store-ports.md), which described `Cache` and
`LlmProvider` as *the* two non-store ports — that page is **amended**: there
are now three, and the reasoning it gives for why a provider port stays narrow
applies unchanged to this one.
[ADR 0012](0012-no-ann-index-in-a-multi-tenant-vector-store.md) **stands**.

## Context

`VectorStore` has existed since slice 5 with two adapters and a compliance
suite, and **nothing in the library could put a vector in it.** Every write
path takes vectors the caller computed elsewhere. `VectorProjection` folds what
it is handed; `CandidateFinder` reads what is already there.

That makes the port list asymmetric in a way that shows up as a hole in the
library's story rather than as a missing convenience. `LlmProvider` is what
lets extraction turn text into entities without knowing there is a model
anywhere; there was no equivalent for turning text into a vector. A caller who
wanted semantic search had to run an embedding model themselves, match this
library's chunking, construct `VectorRecord`s by hand, and keep their model's
dimension in step with the store's — at which point the port is doing very
little for them.

It was reported by the first downstream project to build on redstring, as the
gap that mattered most to them. It was not found by any gate
here, which is itself informative: **nothing tests that a port is reachable.**
The compliance suites prove each adapter satisfies its contract; no check asks
whether the pipeline can produce the contract's inputs.

## Decision

### `EmbeddingProvider` is a port beside `LlmProvider`, not beneath it

`ports/embedding_provider.py`, and adapters under `llm/`. The layer contract is
unchanged: `llm` is already a sibling of `extraction` precisely so extraction
can reach `ports.llm_provider` and never a client library, and embeddings get
the same treatment for the same reason.

It is a **separate port from `LlmProvider`**, not a method added to it. The two
have different implementations in every real deployment — a chat endpoint and
an embedding endpoint are different models, often different servers, and
frequently one is present without the other. Folding `embed` into `LlmProvider`
would oblige `FakeLlmProvider` and every future adapter to implement a
capability they may not have, and would make "extraction works, embeddings are
not configured" unrepresentable.

### `embed` takes a sequence and returns one vector per input

```python
async def embed(self, texts: Sequence[str]) -> list[list[float]]
```

Batch, not single. Every embedding API charges and rate-limits per request
rather than per input, so a one-at-a-time port would put the adapter's single
most important optimisation out of reach — and a caller would rebuild batching
above the port, badly, against a rate limiter it cannot see.

The return is **positional**: one vector per input text, in the same order,
same length. That is the contract a caller needs in order to zip results back
onto entities, and it is the thing an adapter is most likely to get wrong when
it batches internally or retries a partial failure. The compliance suite
asserts it with inputs that are distinguishable from one another, because a
suite that embeds `["a", "a", "a"]` cannot see a reordering.

### The provider declares `dimension`, and disagreement fails at wiring

This is the decision the port exists to force, and there were three candidates:

1. **The provider declares a dimension; the composition point checks it against
   the store's before anything is embedded.** — chosen
2. The store asks the provider at construction and configures itself.
3. Neither declares; the mismatch surfaces when the database rejects the write.

(3) is what happens today for a caller doing this by hand, and it is the worst
of the three: the error arrives from pgvector, after the embedding call has
been paid for, naming a column type rather than a configuration mistake.

(2) is tempting and wrong here. `VectorStore.dimension` is not a free
parameter — pgvector's column type fixes it at DDL time, and ADR 0002 already
records that **changing embedding model means a new store, not an in-place
change**, because two models' vectors are not comparable even at equal
dimensions. A store that reconfigured itself from whatever provider it was
handed would make that mistake silent and easy.

So (1): both sides state a number, and the composition point refuses to wire
them together when the numbers differ. The check is in `composition.py` rather
than in either port, because it is a statement about a *pair* and neither half
owns it.

**The comparison is `!=`, and this is not a style note.** CLAUDE.md records a
dimension check written with `is not` that passed every test at a test
dimension of 8 and rejected every legitimate write at 768, because CPython
caches small integers. The compliance suite therefore requires an adapter to
declare a realistic dimension in at least one case, and the wiring test uses
768.

### Errors

One new exception, `EmbeddingProviderError`, a `RedstringError`. It covers an
adapter failing to produce vectors at all or producing the wrong number of
them.

Dimension disagreement reuses the existing `DimensionMismatchError` rather than
adding a parallel type: it already means exactly this, `VectorStore` already
raises it per-write, and a caller writing `except DimensionMismatchError`
should not have to know whether the mismatch was caught early by wiring or late
by a store.

## Consequences

**The vector half of the library becomes reachable from its own pipeline.**
`build_graph` gains two optional parameters — an `embedding_provider` and a
`VectorStore` — and populates both stores when given both. Supplying one
without the other is a `ValueError` at the call rather than a silent no-op,
because "I configured embeddings and got no vectors" is the failure this whole
ADR is about.

**A third `Test*Compliance` suite exists**, and every `EmbeddingProvider`
adapter runs it unchanged. `FakeEmbeddingProvider` — deterministic, hashes text
into a unit vector of whatever dimension it is asked for — is the in-memory
reference, and it is what makes the commit gate able to exercise the vector
path with no model.

**`FakeEmbeddingProvider` is exported**, unlike most test doubles, for the same
reason `FakeLlmProvider` is: a caller cannot write a test for their own
pipeline without one, and the alternative is every downstream project writing
the same hash-into-a-unit-vector by hand.

**Reproducibility is directional, and the contract says so.** The port
promises that the same text embeds to the same vector; it does *not* promise
bit-identity, because no real backend provides it. Batch composition changes
floating-point accumulation -- measured at up to `4e-3` per component against
llama.cpp -- so the compliance suite compares by cosine above a stated
threshold, with an explicit check that mismatched pairs fall far below it.

That clause was wrong when this ADR was accepted. The suite asserted `==`, and
it passed, because both adapters behind it at the time were exactly
reproducible: a hash and a stub. The first run against a live endpoint failed
two clauses. **The failure mode is worth recording because it is the inverse of
the usual one** -- `recurring-defects.md` §1 warns about an in-memory reference
being *more forgiving* than production, and this was a shared contract stricter
than production can be. It is the more dangerous direction: the natural repair
is an exemption for the real adapter, after which the suite describes the fake
permanently. The fix was to weaken the shared claim exactly as far as the
backend forces, which is the same move `tests/compliance/vector_store.py`
already makes for float32 storage.

**What this does not do.** It does not chunk for embedding: entity text is
short and embedded whole. A document-level embedding, or a chunk-level one for
retrieval, is a different feature over the same port and is not built. It also
makes no attempt to keep vectors in step with later merges — a consolidated
entity's vector is stale until it is re-embedded, which is
[`BACKLOG.md` B67](https://github.com/tyevans/redstring/blob/main/BACKLOG.md)'s
territory rather than this port's.
