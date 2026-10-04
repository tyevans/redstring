# The chunk corpus: stored passages, and what they know about the graph

**Date:** 2026-08-07
**Status:** approved, not yet implemented
**Scope:** B1 of a two-part plan. B2 — chunk embeddings, a term-weighted
ranker, and a public chunk retrieval surface — is deliberately not in this
spec.

## The problem

`SlidingWindowChunker` splits a document, hands the pieces to the LLM, and
throws them away. Everything downstream of extraction therefore knows *that* an
entity came from a document and not *which passage said so*, and there is no
corpus over which a term-weighted ranker could compute a statistic.

ADR 0022 named both consequences and deferred both. This spec builds the
corpus. It does not build the ranker.

## What changes about the library's stance

ADR 0022 argued in part from "this library stores no text". That sentence
described what had been built; it was not a decision. The rules that *were*
decided are two, and neither is weakened here:

- **The library never fetches content.** A caller supplies every byte, before
  and after this change.
- **Extraction writes to no store.** It emits events; projections write.

Retaining a caller-supplied passage violates neither. The ADR for this spec
records the separation, and `CLAUDE.md` already carries it as of the commit
preceding this one.

## Decomposition, and why this half is first

- **B1 (this spec).** The corpus: a domain type, a port, two adapters, a
  compliance suite, an event, a projection, and the two write paths that emit
  it.
- **B2 (later).** Chunk embeddings, real BM25 over the corpus, `ScoredChunk`,
  `retrieve_chunks`, fused with the RRF that part A already built.

B1 is first because B2's every decision is downstream of what a stored chunk
*is*. Guessing a search signature before the corpus exists is how a port
acquires a method its adapters cannot implement the same way.

The port therefore ships with **no search method**. Adding one to our own port
later costs nothing; shipping the wrong one costs an adapter divergence.

## Domain types

`src/redstring/domain/chunk.py`:

```python
ChunkId = str


class StoredChunk(BaseModel):
    id: ChunkId
    tenant_id: TenantId
    source_id: SourceId
    text: str
    chunk_index: int
    start_char: int
    end_char: int
    entity_ids: list[EntityId] = []
    metadata: dict[str, Any] = {}
```

**This is not `extraction.chunking.Chunk`, and the two must not be merged.**
That one is a dataclass in the extraction layer describing a split in progress:
transient, tenantless, and consumed within a single pipeline run. This one is a
stored record. They share four field names and no lifetime. A shared base class
would put the extraction layer's type into the domain and give the transient
one a tenant it has no way to fill.

`text` is validated by `domain/json_safety.reject_unstorable_text`, as
`Entity`, `Relationship` and `VectorRecord` already are. A NUL byte in a
passage is not hypothetical — it arrives from PDF extraction — and Postgres
rejects it at `INSERT` rather than at the boundary.

### `entity_ids`, and the sentence that is the whole contract

**An empty `entity_ids` means no entities were extracted from this passage. It
does not mean extraction is pending.**

This has to be stated in those words on the type, because it is legitimately
empty for an entire class of chunks: everything arriving through the direct
ingest path, which never calls an LLM. Any code that reads emptiness as
"not yet processed" will be wrong forever and will look reasonable in review.

The link points from chunk to entities and is stored on the chunk record, not
as an edge in the graph store. `GraphStore` and `ChunkStore` are separate
ports; a join across them is the caller's business, and putting a chunk
reference into the graph would give `mapping.py` a second id scheme to keep in
step — the specific hazard that kept `consolidation` a sibling layer rather
than a consumer of extraction.

## Identity: content-addressed

```python
def chunk_id(source_id: SourceId, text: str) -> ChunkId: ...
```

A hash over `(source_id, text)` using the text **exactly as stored**, with no
normalisation applied. Two passages differing only in whitespace are two
passages — their `start_char`/`end_char` differ, so collapsing them would give
one id two offsets — and normalising here would create a second scheme to keep
in step with the one in `mapping.py`.

It lives in `domain/chunk.py` and not in `extraction/mapping.py`. Both write
paths need it, `extraction` is a sibling layer, and the domain is the only
place both can reach.

**The source id is part of the hash.** Identical boilerplate under two
documents is two chunks; otherwise one document's `entity_ids` would attach to
the other's passage, and the two would fight over the same row on every replay.

**Positional identity was rejected.** `(source_id, chunk_index)` is simpler and
makes re-chunking an in-place overwrite, which is exactly the defect: chunk 3
of a re-chunked document is a *different passage* wearing the same id, so its
stored entity links and — in B2 — its stored embedding would silently describe
text that no longer says what they claim. Content addressing makes re-chunking
produce new ids and leaves the old ones wrong-but-identifiable rather than
lying.

The cost is orphans, and it is paid in the port.

## The port

`src/redstring/ports/chunk_store.py`, tenant-scoped throughout, with no
cross-tenant read. Like `GraphStore` and `VectorStore` it is a **projection**:
the log is the authority and every write is idempotent because handlers replay.

| Method | Contract |
|---|---|
| `upsert_many(chunks)` | Idempotent, last-write-wins per `(tenant_id, id)`. Records may span tenants. One statement, not a loop. |
| `get(chunk_id, tenant_id)` | The record or `None`. Unknown id is not an error. The result is the caller's; mutating it cannot change stored state. |
| `get_by_source(source_id, tenant_id)` | Every chunk of that source, ordered by `chunk_index` ascending, ties broken by `id`. |
| `replace_source(source_id, tenant_id, chunks)` | Write the incoming set and delete that source's chunks absent from it. See below. |
| `delete_by_source(source_id, tenant_id)` | Count removed. Idempotent. |
| `delete_by_tenant(tenant_id)` | Count removed. No other tenant touched. |

**`replace_source` is one call and not an upsert followed by a delete**, so
that folding one `DocumentChunked` event is a single atomic operation. Split in
two, a crash between them leaves a corpus that is neither the old chunking nor
the new one, and — once B2 lands — leaves document-frequency statistics
computed over a set that never existed. An empty `chunks` argument is legal and
means "this source now has no chunks"; it is not a no-op guard.

`get_by_source` orders by `chunk_index` **and then by `id`**, because
`chunk_index` is not unique under content addressing: a re-chunk landing
mid-replay can transiently produce two chunks claiming index 3. Ordering on the
index alone would let the two adapters disagree about which comes first.

## Write model

A `DocumentChunked` event in `events/document.py`, carrying `source_id` and the
full chunk payload. `projections/chunk.py` folds it by calling
`replace_source` — a whole document's chunking in one event and one call, which
is what makes the fold atomic and the replacement correct.

**Two paths emit it, and neither bypasses the log:**

- **Extraction.** The pipeline already produces chunks and already knows which
  entities each produced. It emits `DocumentChunked` alongside
  `DocumentExtracted`, with `entity_ids` populated.
- **Direct ingest.** A new `composition/index_documents.py` chunks and emits,
  with no LLM call and `entity_ids` empty. This is what makes the corpus
  affordable for a caller who wants passages and not a graph.

`index_documents` belongs in `composition` because it joins `extraction` (the
chunkers) and `projections` — the same pair `build_graph` names, so the layer
rule is satisfied by the argument already recorded rather than a new one. The
layer's requirement is that a module name the pair of mutually-forbidden layers
it joins; this one does.

**A `SourceId` arriving by both routes is last-write-wins on the whole source**,
which follows from `replace_source` and needs no special case: indexing a
document and later extracting it replaces the chunk set with one carrying entity
links, and the reverse order drops them. That is a real behaviour, not an
accident, and it is documented on `index_documents` — a caller who ingests after
extracting has silently discarded the graph links.

## Layer placement

A new top-level `chunks` package in the sibling band, holding
`chunks/adapters/memory.py` and `chunks/adapters/postgres.py`.

`containers = ["redstring"]` with `exhaustive = true` means a new top-level
package **fails the contract until it is placed deliberately**. Placing it
requires, in the same commit that creates it:

- the layer list and its inline reasoning in `pyproject.toml`
- the layer diagram in `CLAUDE.md`, which is binding instruction and which a
  stale copy of would send the next author to a package that does not exist
- a fifth row in `tests/unit/test_dependencies_stay_confined.py`:
  `asyncpg` confined to `chunks/adapters/`

That last one is not optional bookkeeping. Three of the existing four rows were
confined by convention alone until slice 11, each correctly placed and each one
commit from not being.

`chunks` sits beside `graph` and `vector` rather than above or below them: it
holds a projection target, it needs nothing from either, and neither needs
anything from it. A caller joining a chunk to its entities holds both ports,
which is the same shape as every other cross-store question in this library.

## Testing

A shared compliance suite, as `GraphStore` and `VectorStore` have, run against
both adapters. Plus the introspection gate that derives the read-method list
from the Protocol and fails when one lacks a registered mutation-isolation test
and tenant-isolation test — `GraphStore` has this and `ChunkStore` gets it in
the same edit, because the written rule is what failed the first four times.

Cases written against named rows of the failure-shape table in `CLAUDE.md`:

- **Two tenants holding the same `ChunkId`.** Content addressing makes this
  *easy* to produce rather than astronomically unlikely — the same passage
  under the same source id in two tenants hashes identically — so a
  `(tenant_id, id)` key compared on `id` alone is a live defect here, not a
  theoretical one. (Composite-key row, and the one this spec is most exposed
  to.)
- **`replace_source` with one orphan followed by one survivor.** On a
  one-element remainder `break` and `continue` are the same function.
  (Loop row.)
- **`replace_source` with an empty incoming set**, asserting the source is
  emptied rather than left alone. (The guard that "looks defensive" and is
  wrong.)
- **`replace_source` against a source that has never been written.** At least
  one test per stateful path must start from genuinely nothing, or the setup
  is unverified however many tests depend on it. (Fixture row.)
- **`get_by_source` with two chunks sharing a `chunk_index`.** Ties never
  coinciding is the failure shape this repository has hit twice, two years and
  two modules apart. The ordering rule exists for this case, so a test must
  produce it.
- **Mutation isolation on every read**: mutate the returned `StoredChunk` —
  including appending to `entity_ids`, which a shallow copy leaves shared — and
  assert a later read is unaffected.
- **A `StoredChunk` built directly, not through a factory.** If every test
  builds through a helper that passes all fields, the type's own defaults —
  `entity_ids` and `metadata` — never execute. (Factory row.)
- **Replay:** fold the same `DocumentChunked` twice and assert the corpus is
  identical, then fold a re-chunked version and assert the orphans are gone.
  The expected state is recorded independently of the projection, not produced
  by it — an equivalence whose two sides share the fold under test is preserved
  exactly by the bugs that drop work.

Before any hypothesis property is trusted, the implementation is broken on
purpose and the property watched to fail.

## Public surface

New exports: `StoredChunk`, `ChunkStore`, `ChunkProjection`, `index_documents`,
and whatever report type `index_documents` returns. `ChunkId` is `str`, so it
adds no closure; `StoredChunk` names `TenantId`, `SourceId` and `EntityId`,
all exported already.

The end-to-end example gains an indexing step importing nothing but
`redstring`.

## Out of scope

- Chunk embeddings and chunk vector search (B2).
- BM25, and any term statistic (B2).
- `ScoredChunk`, `retrieve_chunks`, or any change to `Retriever` (B2).
- A search or filter method on `ChunkStore` (B2 defines it, against a corpus
  that exists).
- A Neo4j chunk adapter. Postgres is where text belongs, and a second adapter
  exists to prove the port is a contract rather than a description of one
  implementation — the in-memory one does that.
- A version bump. This lands unreleased.

## Documentation

- An ADR recording the separation of "never fetches" from "never stores", the
  content-addressed identity with positional rejected, and `replace_source` as
  one operation. It amends ADR 0022's premise without disturbing its decision,
  per the convention that an ADR is a record rather than a current-state
  document.
- A how-to under `docs/how-to/` for indexing documents without extraction.
- `CLAUDE.md` and `pyproject.toml` layer notes updated in the commit that
  creates the `chunks` package.
- `BACKLOG.md` entries for anything noticed and not fixed, in the same commit
  that passes it by.
