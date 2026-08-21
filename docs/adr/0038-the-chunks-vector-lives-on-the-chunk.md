# ADR 0038: The chunk's vector lives on the chunk

## Status

Accepted. Amended by
[`0044` a chunk id is derived, not supplied](0044-a-chunk-id-is-derived-not-supplied.md),
which closes the executable half this ADR left open below.

Amends [`0023` the chunk corpus](0023-the-chunk-corpus.md) in its
Consequences: the semantic search it deferred is now built, and the port
docstring that said there was no such method is corrected. No decision
0023 made is reversed. Amends
[`0026` `ChunkStore` and `Cache` are composed from capabilities, like
`GraphStore`](0026-chunk-store-and-cache-are-capabilities-too.md): a fifth
capability, `SemanticCandidateSource`, joins the four it named.

[`0002` two store ports](0002-two-store-ports.md),
[`0006` the public surface is gated](0006-the-public-surface-is-gated.md),
[`0012` no ANN index in a multi-tenant vector store](0012-no-ann-index-in-a-multi-tenant-vector-store.md),
[`0017` the embedding provider port](0017-the-embedding-provider-port.md),
[`0022` the lexical channel is not BM25](0022-the-lexical-channel-is-not-bm25.md)
and
[`0024` BM25 over the chunk corpus](0024-bm25-over-the-chunk-corpus.md)
**stand**.

## Context

`ChunkStore` has a lexical recall channel, `LexicalCandidateSource`, and no
semantic one. A caller who has embedded a corpus cannot ask "which chunks are
nearest this vector" without going around the port. `VectorStore` already
answers a structurally identical question for entities, so the first question
is whether a chunk's embedding belongs there instead of on the chunk store.

It does not. `VectorStore` is keyed on an arbitrary vector id and carries
metadata a caller filters on; it has no notion of a chunk's other fields —
`source_id`, `chunk_index`, `entity_ids`, the text itself — and giving it one
would mean either duplicating `StoredChunk`'s identity into a second store (a
new instance of the divergence `.claude/rules/recurring-defects.md` §1
exists to catch — two records of one fact, kept in sync by nothing) or making
`ChunkStore` responsible for handing the vector store the same id on every
write, which is a distributed-transaction problem this library has
consistently avoided by keeping a fact in one store. The vector lives where
the chunk lives: as a nullable column on `StoredChunk`, read back by the same
`get`/`get_by_source`/`get_by_entity` methods that already return the chunk,
and searched by a new capability on the same port.

`0024` moved BM25 scoring into the domain because two adapters implementing
a ranking *formula* could compute different numbers from the same recall set
and disagree silently while agreeing on which chunks matched — the ranking
diverges, not the membership. Cosine similarity does not have that failure
mode: there is one definition, and `VectorStore.search` already relies on
each adapter computing it correctly. Requiring the domain to score would mean
shipping every candidate's full vector across the port so it could be dotted
in Python, which is a real cost for no correctness gain — the thing `0024`
protects against, formula divergence, cannot happen here. What the two
adapters *can* still disagree about is order and cutoff: which chunks a tie
in score resolves to, and which candidates a `limit` truncates. So the port
states a total order — score descending, ties by `id` ascending — and the
compliance suite (a later task) asserts both adapters agree on it, exactly
as `lexical_candidates` is pinned today.

A chunk store serves callers with different embedding models, and a search
issued with a vector of the wrong width is not an error the store can detect
by inspecting the vector alone — it needs to know its own configured width.
`VectorStore` already carries this as a constructor argument; `ChunkStore`
gains the same requirement through `SemanticCandidateSource.dimension`,
declared once at construction rather than inferred from whichever chunk
happens to be embedded first. An optional width — leaving
`semantic_candidates` raise until the first embedded write told the store
what to expect — would let a store satisfy the capability's structure without
satisfying its behaviour, which is worse than requiring the argument.

Something has to call the embedding provider and the chunk writer in the
same breath, and neither `extraction` nor `chunks` may. Extraction writes to
no store, by the library's standing rule; `chunks` is a sibling of `llm` in
the layered contract and may not import an embedding provider. `composition`
is the layer built to hold exactly this kind of pair — it already drives
`ChunkWriter` from `index_documents` — so embedding a corpus is a
`composition`-layer decision, made by passing an optional
`EmbeddingProvider` through, not a capability either sibling package
acquires.

## Decision

**Add `SemanticCandidateSource` as a fifth capability composed into
`ChunkStore`, alongside `ChunkWriter`, `ChunkReader`, `LexicalCandidateSource`
and `ChunkPurge`:**

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

**The adapter computes the similarity score.** This is a stated exception to
`0024`'s "the domain scores" rule, not a reversal of it: `0024`'s argument was
about a ranking *formula* that two adapters could implement differently,
which is a property of BM25 and not of cosine similarity. The port pins the
one thing that can still diverge — total order, score descending then `id`
ascending — so the compliance suite still asserts the adapters agree on
results even though it does not recompute their arithmetic.

**The store declares its width at construction.** `dimension` is a required
property, not an optional one a store discovers from its first write. Both
adapters (a later task) take it as a constructor argument, the same shape
`VectorStore` already uses.

**Composition embeds; extraction and `chunks` still do not.** The pipeline
module that drives `ChunkWriter` gains an optional `EmbeddingProvider`
argument (a later task) rather than either sibling package acquiring a new
import. A corpus indexed without one is stored exactly as today, with
`embedding=None`; chunks with `embedding is None` are not semantic
candidates — skipped, not scored zero, for the same reason an absent
component score means "unranked" rather than "ranked at zero" on
`ScoredChunk`.

**No ANN index.** `0012` already argues that an approximate index over a
multi-tenant table either crosses tenant boundaries or is built per tenant,
and that argument does not depend on which table carries the vector. The
chunk-corpus scan is exact and linear in the tenant's corpus for the same
reason the entity vector store's is, and `0012` governs this instance without
being relitigated.

## Consequences

The port composes five capabilities instead of four; the public export
surface grows by `SemanticCandidateSource`. `ChunkStore`'s docstring no longer
claims there is no semantic method here, correcting the sentence `0023`
wrote before this work existed.

The two adapters implementing `SemanticCandidateSource`, and the compliance
cases pinning its ordering, `min_score`, `limit = 0` and negative `limit`,
unembedded-chunk exclusion, and the two isolation cases the coverage gate
names by convention, are later work. Until they land, `InMemoryChunkStore`
and `PostgresChunkStore` satisfy `ChunkWriter`, `ChunkReader`,
`LexicalCandidateSource` and `ChunkPurge` but not the composed `ChunkStore`,
which is a correct and visible consequence of widening the port ahead of its
adapters rather than a defect to paper over.

Content addressing on `ChunkWriter.upsert_many` is stated as prose here
because the new column inherits it: `embedding` is derived from a chunk's
text the same way the lexical term index and `doc_length` are, so a write
that reuses an id for different text is outside the contract for the same
reason those two are. The executable half stayed open at the time this ADR
was written; see
[`0044`](0044-a-chunk-id-is-derived-not-supplied.md), which closes it by
making the id a computed field instead of adding a compliance case for a
state a caller can no longer construct.
