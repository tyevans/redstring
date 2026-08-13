# B2b: a semantic channel over the chunk corpus, and a fused `retrieve_chunks`

**Status:** design, approved for planning.

**Goal.** Give the chunk corpus a second retrieval channel — stored
embeddings, searched exactly — and a public entry point that fuses it with
the BM25 channel B2a built. This is the piece B2a named as deliberately
downstream: *"a public result type carrying two component scores cannot be
designed correctly until both components exist."* Both now do.

**Not in this work.** An ANN index (ADR 0012 refuses it, and refuses it again
here); incremental corpus statistics (B92); re-ranking; chunk-to-entity
fusion in one call. Each is argued below or filed.

## Context

B1 built the corpus (ADR 0023) with no search method. B2a built the lexical
half (ADR 0024): recall and statistics in the adapter, scoring as a pure
function in `domain/`, so two adapters rank identically and the compliance
suite asserts it. What is missing is everything semantic — `StoredChunk`
carries no vector, `ChunkStore` has no method that takes one, there is no
`ScoredChunk`, and `composition/retrieval.py` fuses two channels for
*entities* while the chunk corpus has one channel and no entry point at all.

Four decisions B1 and B2a already made constrain this work, and B89 records
them so they are not rediscovered:

- **Chunk ids are content-addressed over `(source_id, text)`.** A re-chunk
  produces new ids rather than overwriting. This is what makes a stored
  embedding safe to keep at all: a vector can never silently come to describe
  text that has changed. It was chosen for this reason before any embedding
  existed, and identity is not simplified to `(source_id, chunk_index)` here.
- **`replace_source` is one atomic call.** Anything this work stores per
  chunk is written by that call and no other.
- **BM25 is not a name for the entity lexical channel.** ADR 0022 stands. The
  channel added here is a *third* one — semantic over passages — beside the
  string similarity over names and the BM25 over passages. It replaces
  neither.
- **`entity_ids` is on the chunk, not in the graph.** Turning a ranked
  passage into entities is the caller's move, holding both ports.

## Decision

### The chunk's vector lives on the chunk, not in `VectorStore`

`StoredChunk` gains `embedding: list[float] | None = None`.

The tempting alternative is to reuse `VectorStore`, which already stores
vectors and already searches them exactly. It is rejected because that port is
entity-shaped all the way down: it is keyed by `EntityId`, `search` returns
`VectorMatch(entity_id=...)`, and its one filter is `entity_types`. Storing
chunk vectors there means either a second id scheme inside a single store or
an `EntityId` that does not name an entity — and ADR 0002's boundary argument
is precisely that a port answers one question about one kind of thing.

The positive reason is stronger than the avoidance. The property that makes a
stored embedding trustworthy is content addressing, and content addressing is
a fact about the *chunk row*: the id is the digest of the text the vector
describes, so the two cannot drift apart, and a re-chunk writes a new row
rather than invalidating an old one. Putting the vector anywhere else breaks
that guarantee into two halves held by two stores with no transaction between
them.

`embedding` defaults to `None` and `None` is not zero. It means *this passage
has not been embedded*, which is an ordinary state during a partial rollout
and is distinct from a vector of zeros.

### A fifth capability: `SemanticCandidateSource`

ADR 0026 composed `ChunkStore` from four capability protocols so that a
collaborator depends on the methods it calls and not on nine. A fifth joins
them:

```python
class SemanticCandidateSource(AsyncClosable, Protocol):
    @property
    def dimension(self) -> int: ...

    async def semantic_candidates(
        self,
        vector: Sequence[float],
        tenant_id: TenantId,
        limit: int,
        *,
        min_score: float | None = None,
    ) -> list[SemanticCandidate]: ...
```

`SemanticCandidate` is `chunk: StoredChunk` and `score: float`.

**The adapter scores here, and the asymmetry with BM25 is the point.** ADR
0024 moved scoring into the domain because two adapters could implement *a
ranking formula* two ways and diverge silently while agreeing on which chunks
matched. Cosine similarity is not that kind of formula — there is one
definition, `VectorStore` already relies on the adapter computing it, and
pushing it into the domain would mean shipping every candidate's full vector
across the port to score in Python. What can still diverge is **order**, not
value: two adapters can compute the same similarities and disagree on ties, or
on rows the filter should have excluded. So the port states the total order —
score descending, ties by `id` ascending — and the compliance suite asserts
the two adapters return the same chunks in the same order, and equal scores
within a tolerance.

**The store declares its width at construction, and both adapters require
it.** pgvector's column type is `vector(n)`, which bakes the dimension into
the DDL, so a chunk table is a table of one width exactly as `PgVectorStore`'s
already is — `dimension` is a required constructor argument on
`PostgresChunkStore` and on `InMemoryChunkStore`, not an optional one. This is
a **breaking constructor change** for both, and it is the honest shape: the
alternative, an optional width that leaves `semantic_candidates` raising and
the `dimension` property undefined, means a store that satisfies
`SemanticCandidateSource` structurally and not behaviourally — a Protocol
implemented conditionally, which is worse than a constructor argument.

The similarity expression is shared with the vector store rather than
rewritten: `1 - (embedding <=> $2::vector) / 2`, pgvector cosine distance
mapped to a 0..1 similarity. Two stores in this library computing "cosine
similarity" by two expressions is the divergence ADR 0024 is about, one port
over.

Chunks with `embedding = None` are not candidates. They are skipped, not
scored as zero: a null vector has no similarity to anything, and scoring it
zero would let an unembedded passage outrank a genuinely dissimilar one.

`limit = 0` returns nothing; a negative `limit` raises `ValueError`. Both
match `lexical_candidates` and `VectorStore.search`, and `k = 0` is pinned as
an explicit example rather than left to a hypothesis sampler (B125's lesson,
and the `k < 0` / `k <= 0` mutants that killed non-deterministically).

**No ANN index. ADR 0012 governs and is not relitigated.** The scan is exact
and linear in the tenant's corpus, for the reason 0012 gives: an
`hnsw`/`ivfflat` index over a multi-tenant table either indexes across tenant
boundaries or is built per tenant, and the first is a correctness problem
rather than a performance trade. B10k already records the same cost for the
vector store; this work adds a second instance to that entry rather than a
new one.

### One exception type for a dimension mismatch, everywhere — B82 closed

`SemanticCandidateSource` declaring `dimension` creates the third composition
point that must refuse a provider and a store of different widths — exactly
the instance B89 predicted. B82 has been open since the second one appeared,
and it does not survive a third.

Today `Retriever.__init__` raises `DimensionMismatchError` and
`build_graph`'s `_check_embedding_wiring` raises `ValueError` for the same
condition, and neither `except` catches the other. **`DimensionMismatchError`
wins.** It is the type that names the condition, `RedstringError` is the
family a caller catches, and a `ValueError` here is indistinguishable from
every other argument complaint.

This is a **breaking change** for anyone catching `ValueError` around
`build_graph`, and it takes a version bump. That is why B82 was filed rather
than fixed on the spot, and why it is being done in a piece of work that
already ships a schema change.

`build_graph`'s `ValueError` covers a second condition — a provider supplied
without a store, or the reverse — and **that one stays a `ValueError`.** The
two are different mistakes: half-configured wiring is an arity error about
the arguments, and a dimension mismatch is a disagreement between two
correctly-supplied collaborators. Collapsing them would lose the distinction
to buy a uniformity that was never the complaint.

**The test comes first and is currently red.** B82's closing paragraph is the
part worth honouring: nothing asserts that the composition points *agree*, so
a fourth would diverge unnoticed, which is `recurring-defects.md` §1 at the
composition layer. The gate enumerates the composition entry points that take
an `EmbeddingProvider` and asserts each refuses a mismatched pair with
`DimensionMismatchError` — so a new one is covered by construction rather
than by whoever remembers.

### `ScoredChunk` mirrors `ScoredEntity`, including what `None` means

```python
class ScoredChunk(BaseModel):
    chunk: StoredChunk
    score: float  # fused
    semantic: float | None = None
    lexical: float | None = None
```

`None` on a component means **the channel did not rank this chunk**, not that
it scored zero. That distinction is ADR 0022's, stated there because RRF
discards magnitude and the component scores are the only way a caller can see
what fusion threw away. It is worth restating because the two readings differ
exactly where a caller debugs a surprising ranking.

`ScoredChunk` carries the whole `StoredChunk`, not an id. The caller already
paid for the row — both channels return chunks — and handing back an id would
force a third read to display the passage that was just ranked.

### `reciprocal_rank_fusion` becomes generic

It is typed on `EntityId` today and its tie-break is "canonical lowercase
`EntityId` string". Chunks need the same function, so it takes a type
parameter bounded by what it actually requires: a hashable id with a stable
`str`. The tie-break becomes the id's string, which for a `ChunkId` — a hex
digest — is already canonical and lowercase.

`RRF_K = 60` stays a module constant and stays unparameterised, per ADR 0022.

**The tie-break is what makes this safe to share, so it is what the tests
pin.** A fusion whose order depends on dict iteration would pass every
existing test and diverge between two channels' orderings; the property to
assert is that fusion is a *total* order over ids, with ids pinned rather than
drawn from `uuid4()` — the failure this project has now filed three times.

### `ChunkRetriever` is a second class in `composition/retrieval.py`

```python
class ChunkRetriever:
    def __init__(
        self,
        *,
        embeddings: EmbeddingProvider,
        chunks: LexicalCandidateSource & SemanticCandidateSource,
        overfetch: int = 3,
    ) -> None: ...

    async def retrieve_chunks(
        self,
        query: str,
        tenant_id: TenantId,
        *,
        k: int = 10,
        mode: RetrievalMode = RetrievalMode.HYBRID,
    ) -> ChunkRetrievalResult: ...
```

It is a separate class from `Retriever` rather than a method on it, because
the collaborator sets are disjoint: `Retriever` needs a graph and a vector
store and no chunk store, and this needs a chunk store and neither of the
others. One class taking five collaborators to serve two independent queries
is how a composition root becomes a god object.

**It satisfies ADR 0007's admission test on its own**, not merely by living
in a module already admitted: it joins `llm` and `chunks`, two siblings
forbidden from importing each other. A chunker's store may not import an
embedding provider and the `llm` package may not import a store; something
has to hold both.

Blank query raises, negative `k` raises, `k = 0` returns empty — the same
three rules `Retriever.retrieve` states, because a caller holding both should
not have to remember which one is stricter. `overfetch` carries the same
meaning and the same `>= 1` floor, and the reasoning (fusion is decided by
the candidates just past each channel's cutoff) is unchanged.

There is no `entity_types` filter. The chunk analogue would be a filter on
`entity_ids`, and ADR 0023 already refused to put that on the ranked path:
"which passages mention this entity" is graph navigation, not relevance, and
folding it in makes one method answer two questions under one `k`.

### Composition embeds; extraction still does not

`index_documents` gains an optional `embeddings: EmbeddingProvider | None`.
When supplied, it embeds each chunk's text and stores the vector with the
chunk in the same `replace_source` call; when `None`, chunks are stored with
`embedding=None` exactly as today.

This is the placement the library's two standing rules already force.
Extraction writes to no store, so it cannot be the thing that persists a
vector; `chunks/` is a sibling of `llm/` and may not import an embedding
provider. `composition` is the only layer that may hold both, and
`index_documents` is already the module that drives a `ChunkWriter`.

**`index_documents` promises no per-token cost today, and the promise is kept
by the default.** Its docstring says the function is free of model calls, and
that is why the parameter is optional and defaults to `None`: a caller who
does not pass a provider pays exactly what they pay now, and the docstring's
claim becomes conditional on an argument the caller supplies rather than
false. Embedding is the one model call this function can make, it is opt-in,
and the report counts how many chunks were embedded so the cost is visible in
the return value rather than only on an invoice.

**A corpus with no embeddings answers a semantic query with nothing, and does
not raise.** The alternative — refusing the query — cannot be implemented
honestly, because "unembedded" is a per-row fact and a corpus is routinely
half-embedded during a rollout. A `HYBRID` query over such a corpus still
returns its lexical results, which is the behaviour that makes a staged
backfill usable. The cost is that a caller who forgot to wire a provider sees
silence rather than an error, and that is stated in the `ChunkRetriever`
docstring and in the how-to, where a missing result would otherwise read as a
bug.

### The migration ships, and it carries the one already owed

`ensure_schema` is `IF NOT EXISTS` in every statement, so it adds nothing to
a `kg_chunks` table that already exists. B89 records the consequence: a table
created before the chunk-lexical work never got `doc_length`, and every query
naming `_COLUMNS` fails against it with *column doc_length does not exist*.
That migration is owed already and this work adds a second column to the same
table, so both go in one place:

- `ALTER TABLE {table} ADD COLUMN IF NOT EXISTS doc_length integer NOT NULL DEFAULT 0`
- `ALTER TABLE {table} ADD COLUMN IF NOT EXISTS embedding vector({dim})`

appended to `_schema_statements`, so `ensure_schema` remains the single
idempotent entry point and an existing deployment is repaired by the call it
already makes.

`ADD COLUMN` is not enough for the lexical half: rows written before the
change have `doc_length = 0` and no term rows, so they rank as empty
documents. `PostgresChunkStore.backfill_lexical_index()` recomputes both from
the stored `text` using `domain.tokenize` — the same function the write path
uses, which is what keeps the backfilled rows identical to freshly-written
ones. It returns how many rows it touched, and is idempotent.

Embeddings are **not** backfilled by the adapter, and cannot be: producing a
vector needs a provider the store does not have. A caller re-embeds by
re-running `index_documents` over the affected sources, which is the
documented route.

**`kg_chunks` has never appeared in a tagged release**, so today this repairs
only deployments tracking `main`. That stops being true on the next release,
which is the argument for shipping the repair with the column rather than
after it.

### Content addressing is stated on the port, and its executable half stays open

The new column inherits B97 exactly. `_ON_CONFLICT` omits `doc_length` and
`_TERMS_ON_CONFLICT` is `DO NOTHING`, both justified by content addressing —
a chunk id fixes its text, so a write reusing an id writes the same text and
the derived columns can never need updating. `embedding` is derived from text
the same way and joins them.

That argument is correct only for callers who build ids with `chunk_id`, and
nothing enforces it. This work ships **the cheap half**: the constraint is
stated as prose on `ChunkWriter.upsert_many` — *a chunk id is
content-addressed over `(source_id, text)`; re-using an id for different text
is outside the contract* — so the port says what the adapters assume.

The executable half stays deferred, and B97 is updated rather than closed. A
compliance case asserting the adapters agree after a same-id-different-text
write currently fails on Postgres, and making it pass means updating three
derived columns on conflict — which is a decision about whether the port
promises last-write-wins on derived state, not a test.

## ADRs this design is run against

| ADR | Verdict |
|---|---|
| `0002` two store ports | **Stands.** `ChunkStore` was already a third port, argued in 0023; this adds a capability to it, not a port. |
| `0006` the public surface is gated | **Stands.** New exports go through the three gates, and the signature gate names the closure `ScoredChunk` pulls. |
| `0007` / `0021` composition's admission test | **0021 governs, stands.** `ChunkRetriever` names its pair: `llm` and `chunks`. |
| `0012` no ANN index | **Stands, and governs.** The chunk scan is exact for the same reason; this is a second instance of the cost, not a new decision. |
| `0017` the embedding provider port | **Stands.** The same port, unwidened, serves chunks. |
| `0022` the lexical channel is not BM25 | **Stands.** A third channel; the entity string similarity is untouched. `None`-means-unranked is inherited from it. |
| `0023` the chunk corpus | **Amended** in its Consequences: the semantic search it deferred is now built, and the port docstring saying there is no such method is corrected. No decision reversed. |
| `0024` BM25 over the chunk corpus | **Stands.** The lexical channel is unchanged and becomes one of two. The new channel's adapter-side scoring is a stated exception with its own argument, recorded in the new ADR. |
| `0026` chunk store and cache are capabilities | **Amended.** A fifth capability protocol joins the four. |

**Two new ADRs**, numbered against `main` at merge:

1. *The chunk's vector lives on the chunk* — D1, D2, D5, D7: why not
   `VectorStore`, why the adapter scores when 0024 said the domain does, and
   why composition embeds.
2. *One exception for a dimension mismatch* — D3: closes B82, records the
   breaking change, and records that the half-configured case keeps
   `ValueError`.

## Testing

- **Compliance first.** `semantic_candidates` is a port method with two
  adapters, so its semantics live in `ChunkStoreCompliance` and nowhere else:
  ordering, the `id` tie-break, `limit = 0` and negative `limit` as pinned
  examples, `min_score`, unembedded chunks skipped, and the two
  isolation cases the coverage gate requires by name —
  `test_semantic_candidates_returns_copies` and
  `test_semantic_candidates_never_crosses_tenants`.
- **The tie-break needs colliding scores**, built by hand rather than from
  random vectors. Two chunks at an identical similarity are what distinguishes
  a stated total order from an incidental one, and the ids are pinned so the
  assertion cannot pass on whichever digest happened to sort higher.
- **Fusion needs pinned ids for the same reason**, and a case where a chunk
  ranked outside `k` in both channels beats one ranked first in one — the
  property `overfetch` exists for, which a test drawing ids randomly cannot
  state.
- **The dimension gate is written red first**, before `build_graph` changes.
- **The migration is proved against a table created without the columns**, not
  only against a fresh one: an `ALTER ... IF NOT EXISTS` that runs only where
  the column already exists is a statement that has never been observed to do
  anything.
- **Three gates need deliberate edits, and each fails loudly rather than
  silently — which is why they are named here rather than discovered.**
  `tests/unit/chunks/test_compliance_coverage.py` introspects the port for
  chunk-returning methods, so its `_PORT_NAMESPACE` and the `_mentions` target
  set must both learn the new return type or the gate skips the method
  quietly; its `test_the_port_has_read_methods_to_check` pins the method set
  as a literal and fails the moment one is added, which is the guard-the-guard
  working as designed. `tests/unit/chunks/test_capability_segregation.py`
  needs the fifth protocol.
- **`backfill_lexical_index` is proved to change something**, by asserting a
  pre-backfill ranking is wrong and a post-backfill one is right — a backfill
  asserted only by its return count is a counter, not a repair.

## Consequences

**Retrieval quality is still unmeasured, and this adds a third channel to the
thing that is unmeasured.** B81 records that nothing in this repository can
show the hybrid beats either channel alone: there is no graded retrieval
corpus, and the in-gate provider is a hash-based fake whose vectors carry no
semantics. This work does not change that, and its fusion claim rests on the
same argument-from-failure-modes as ADR 0022's. B81 is updated to name the
chunk channels too.

**Search cost is now linear in the corpus for both channels.** Lexical is
bounded by the candidate `limit`; semantic is a full scan of the tenant's
embedded rows per query, by ADR 0012. B10k gains the second instance.

**A wider `StoredChunk` crosses the port on every read.** `get_by_source`
over a large document now carries a vector per chunk whether the caller wants
one or not. Filed rather than solved: a projection selecting columns is a
port change, and no measurement here says the width costs anything yet.
