# Entity retrieval: a hybrid surface over the stores that already exist

**Date:** 2026-08-06
**Status:** approved, not yet implemented
**Scope:** part A of a two-part plan. Part B (a chunk store and true BM25) is
deliberately not in this spec.

## The problem

`VectorStore`, `EmbeddingProvider`, `VectorProjection` and two adapter pairs
all shipped in 0.3.0. What did not ship is anything that turns **a query
string** into **ranked entities**. A caller today must embed their own query
and call `store.search(...)`, which means every caller reimplements the same
composition, and the library's own answer to "how do I find something" is a
port method rather than a capability.

This spec adds that capability, and adds a lexical ranking beside the semantic
one because embeddings are weakest exactly where entity resolution matters
most: `ACME Corporation` against `Acme Corp` is a cosine coin-flip and a
lexical certainty.

## What this is not

**It is not BM25, and the API never uses that name.** BM25 weights terms by
corpus statistics — document frequency, average document length. Over entity
names those quantities are degenerate: every "document" is a handful of words,
and document frequency over a corpus of names measures something other than
informativeness. Shipping a field-weighted lexical scorer under the name BM25
would promise corpus behaviour the data cannot support.

Real BM25 needs a real corpus, which needs stored text, which this library
deliberately does not keep. That is part B.

## Decomposition, and why this half is first

- **A (this spec).** Retrieval surface over entities, using only what exists.
- **B (later).** `ChunkStore` port, adapters, compliance suite, an ADR
  reversing "the library stores no content", and true BM25 over it. B extends
  the surface A defines with a second backing corpus; it does not add a second
  API.

A is first not merely because it is smaller. A forces two decisions that B
would otherwise make in isolation and then have retrofitted: **how two
rankings fuse into one**, and **what a retrieval result is as a domain type**.
Deciding those against entities, where the stores already exist, makes B's
design cheap instead of speculative.

## Architecture

### `composition` becomes a package

`src/redstring/composition.py` becomes `src/redstring/composition/` holding
`build_graph.py` and `retrieval.py`. `redstring.composition` re-exports both,
so no existing import path changes.

`pyproject.toml` states that a second module in this layer "should have to say
what it composes". Retrieval composes **`EmbeddingProvider` + `VectorStore` +
`GraphStore`**: the provider embeds the query, the vector store ranks, the
graph store turns `EntityId`s back into `Entity`s *and* supplies lexical
candidates. No sibling layer may hold all three — `vector` and `graph` may not
import each other, and neither may import `llm`. That is the same argument
that put `build_graph` here.

ADR 0007 is titled "`composition` is the only top layer" and its first decision
says the layer "holds exactly one module". A new ADR amends that decision
rather than editing 0007 in place, per the repo's convention of ADRs being a
record rather than a current-state document. The layer diagram in `CLAUDE.md`
and the inline reasoning in `pyproject.toml` are updated in the same commit,
since a stale layer diagram in binding instructions sends the next author to a
package that does not exist.

### Domain types

Two new types in `src/redstring/domain/retrieval.py`:

```python
class ScoredEntity(BaseModel):
    entity: Entity
    score: float  # fused, ordinal — see below
    semantic: float | None  # VectorMatch scale, 0..1, None if not ranked semantically
    lexical: float | None  # 0..1, None if not ranked lexically


class RetrievalResult(BaseModel):
    query: str
    matches: list[ScoredEntity]
```

**Component scores are retained, not discarded after fusion.** Without them a
caller cannot distinguish "matched strongly on both channels" from "matched on
name alone", and that distinction is the whole reason for going hybrid. `None`
means *not ranked by that channel*, which is different from *ranked and scored
zero*; the docstring says so, and a test asserts the two are distinguishable.

**`ScoredEntity.score` is not on `VectorMatch`'s scale.** RRF scores are
ordinal: comparable within one result set, meaningless across queries, and
never interpretable as a similarity. `VectorStore`'s own docstring warns at
length about "score" being ambiguous across vector databases; reusing the bare
name for a differently-scaled number would walk into that trap from the
inside. The scale is stated where the type is defined, as
`domain.vector` does for cosine, and `score` carries no `0..1` bound because it
has none.

Both types export. `ScoredEntity` pulls `Entity`'s closure, which is already
exported in full.

### Fusion: reciprocal rank

```
rrf(e) = Σ over rankings containing e of  1 / (k_rrf + rank(e))     k_rrf = 60
```

Chosen over a weighted sum of scores because **the two scores have no common
unit and never will**. Cosine-derived similarity and lexical string similarity
are not commensurable, and any weighted blend silently invents an exchange rate
between them that will be wrong for some corpus and unfalsifiable for all of
them. RRF uses only rank, which both channels genuinely produce.

`k_rrf = 60` is the value from the original Cormack et al. result and is a
module constant with the citation in its docstring, not a magic number and not
a parameter — exposing it would invite tuning against a benchmark this library
does not have.

Ties in fused score break by ascending `entity_id` as its canonical lowercase
hyphenated string, which is exactly the rule `VectorStore.search` already uses.
The result is a total order, so `k` cutting through a tie cannot depend on
backend or dict ordering.

### The lexical channel

Scoring runs over `name`, `normalized_name`, and the string values of
`properties`, with field weights (name above properties). It reuses
`domain.normalization.normalize_name` and `domain.similarity.string_similarity`
rather than growing a second normalisation — a second normalisation scheme is
how two subsystems that agree today disagree in six months, which is the same
hazard `mapping.py` exists to prevent for ids.

**Candidate generation is the load-bearing decision.** There is no text index,
so the scorer cannot scan the corpus. It computes blocking keys from the query
using `domain/blocking.py` and calls `GraphStore.find_by_blocking_keys`, then
scores what comes back.

The consequence is documented rather than left to be discovered:

> **Lexical recall is bounded by blocking.** A query that shares no blocking
> key with an entity cannot retrieve it lexically, however high the string
> similarity would have been.

That is the honest cost of having no text index, and it is the second reason
this is not called BM25. It is stated in the `Retriever` docstring, not only
here.

## API

```python
class RetrievalMode(StrEnum):
    SEMANTIC = "semantic"
    LEXICAL = "lexical"
    HYBRID = "hybrid"


class Retriever:
    def __init__(
        self,
        *,
        embeddings: EmbeddingProvider,
        vectors: VectorStore,
        graph: GraphStore,
    ) -> None: ...

    async def retrieve(
        self,
        query: str,
        tenant_id: TenantId,
        *,
        k: int = 10,
        entity_types: Sequence[str] | None = None,
        mode: RetrievalMode = RetrievalMode.HYBRID,
    ) -> RetrievalResult: ...
```

`StrEnum` per the slice 6 migration. The constructor refuses
`embeddings.dimension != vectors.dimension` with the existing
`DimensionMismatchError` — the same check and the same reasoning as
`build_graph`: fail before an embedding call is paid for, not after pgvector
rejects the insert.

`entity_types` filters both channels. On the semantic side it passes through to
`VectorStore.search`, which applies it **before** `k`; the lexical side must do
the same, and a test asserts it, because filtering after truncation is the
single defect `VectorStore`'s docstring calls out as correct-looking and wrong.

### Error and edge policy

| Condition | Behaviour | Why |
|---|---|---|
| `query` empty or whitespace-only | `ValueError` | Embedding it yields a vector nobody intended and confident nonsense downstream |
| `k == 0` | `[]` | Matches `VectorStore.search` exactly |
| `k < 0` | `ValueError` | Matches `VectorStore.search` exactly |
| `entity_types=[]` | no matches | Matches `VectorStore.search` exactly |
| Vector match whose entity the graph store lacks | skipped, **no backfill to `k`** | See below |

**The dangling-match case is normal, not exceptional.** The vector store and
the graph store are independent projections folding the same log, and they lag
independently by construction. Raising would make retrieval fail during
ordinary replay. Backfilling to `k` from further down the ranking would hide a
projection that had fallen badly behind, turning an operational signal into
silence. Skipping without backfill returns fewer than `k` results, which is
already a legal outcome of a small corpus, and is the only option that neither
lies nor breaks.

## Testing

Beyond behavioural coverage, these cases are written against named rows of the
failure-shape table in `CLAUDE.md`. Each exists because a plausible wrong
implementation would otherwise pass.

- **Fusion with colliding ranks.** Entities appearing in both rankings at the
  *same* rank. Without a collision, "sum the reciprocals" and "take the max"
  agree, and the test distinguishes nothing. (Tie-break row.)
- **Two tenants holding the same `EntityId`.** Ids from `uuid4()` never
  collide, so a `(tenant_id, id)` key compared on `id` alone survives every
  natural test. The table records that this one fires anyway, in a fix round
  that cited it. (Composite-key row.)
- **A lexical candidate scoring zero followed by one scoring well.** On a
  one-element remainder `break` and `continue` are the same function; a bad row
  followed by a good one is what separates them. (Loop row.)
- **A `GraphStore` returning equal-but-distinct `Entity` objects.** Both
  shipped adapters hand back the object they were given, so `is` where `==` was
  meant passes against both. A third test double that rebuilds the entity is
  the only thing that sees it. (Identity row, non-cache form.)
- **`k` boundary as pinned `@example`s** alongside any hypothesis property:
  `0`, `1`, and a value exceeding the corpus size. A property sampling `k` from
  a range makes boundary coverage depend on the sampler and on
  `KG_COMPLIANCE_MAX_EXAMPLES`, which mutation runs lower to 5.
- **Mutation-isolation tests** for every method returning mutable state:
  mutate the returned `RetrievalResult` and assert a later retrieve is
  unaffected.
- **`semantic=None` versus `semantic=0.0`** distinguishable in a result, since
  the type claims they mean different things.

Before any hypothesis property is trusted, the implementation is broken on
purpose and the property watched to fail. A property that stays green under a
deliberate defect is worse than no property, because its existence stops
anyone writing the test that would have worked.

## Public surface

New exports: `Retriever`, `RetrievalMode`, `RetrievalResult`, `ScoredEntity`.
The signature gate requires every type named in an exported signature to be
exported; `EmbeddingProvider`, `VectorStore`, `GraphStore`, `TenantId` and
`Entity` already are, so the closure is satisfied by the four above. The
end-to-end example gains a retrieval step importing nothing but `redstring`.

## Out of scope

- Any chunk or document storage (part B).
- True BM25 (part B).
- Re-ranking with a cross-encoder or an LLM.
- Query expansion or spelling correction.
- ANN indexing — ADR 0012 already decided against it for a multi-tenant store.
- A version bump. This lands unreleased.

## Documentation

- ADR amending 0007's one-module decision, recording what retrieval composes.
- ADR recording why the lexical channel is not BM25 and what blocking-bounded
  recall costs.
- A how-to under `docs/how-to/` for retrieving entities.
- `CLAUDE.md` and `pyproject.toml` layer notes updated in the commit that makes
  `composition` a package.
- `BACKLOG.md` entries for anything noticed and not fixed, in the same commit
  that passes it by.
