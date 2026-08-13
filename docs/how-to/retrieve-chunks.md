# Retrieve chunks

Once passages are in a `ChunkStore` -- via `index_documents` -- `ChunkRetriever`
turns a query string into ranked chunks. It is `Retriever`'s shape over the
chunk corpus instead of the entity graph: it runs two channels and fuses them
by reciprocal rank -- a **semantic** one that embeds the query and searches
the store's vectors, and a **lexical** one that scores BM25 candidates over
the store's term index.

Everything here is in `redstring.__all__`, so nothing in this guide reaches
past the public API.

You will need:

- a `ChunkStore` holding chunks, indexed with `index_documents`,
- an `EmbeddingProvider` of the **same dimension** the store was constructed
  with -- `ChunkRetriever` refuses a mismatched pair at construction, exactly
  as `Retriever` does, and
- chunks that were indexed **with** an `EmbeddingProvider` passed to
  `index_documents`, if you want the semantic channel to find anything. See
  "The limitation to plan around" below.

Read [ADR 0038](../adr/0038-the-chunks-vector-lives-on-the-chunk.md) for why
the vector lives on `StoredChunk` rather than in a second `VectorStore`, and
[ADR 0024](../adr/0024-bm25-over-the-chunk-corpus.md) for the lexical
channel's own design.

## Index, then retrieve

```python
import asyncio
from uuid import uuid4

from redstring import (
    ChunkRetriever,
    FakeEmbeddingProvider,
    InMemoryChunkStore,
    RetrievalMode,
    SourceDocument,
    index_documents,
)


async def main() -> None:
    tenant_id = uuid4()
    embeddings = FakeEmbeddingProvider()
    chunks = InMemoryChunkStore(dimension=embeddings.dimension)

    await index_documents(
        [SourceDocument(id="notes", text="Ada Lovelace worked with Charles Babbage.")],
        store=chunks,
        tenant_id=tenant_id,
        embeddings=embeddings,
    )

    retriever = ChunkRetriever(embeddings=embeddings, chunks=chunks)

    result = await retriever.retrieve_chunks("Charles Babbage", tenant_id, k=5)

    for match in result.matches:
        print(
            f"{match.chunk.id}: {match.score:.4f} semantic={match.semantic} lexical={match.lexical}"
        )


asyncio.run(main())
```

`retrieve_chunks` takes the query, a tenant, and two keyword arguments:

| Argument | Default | Means |
|---|---|---|
| `k` | `10` | Maximum results. `k=0` returns nothing; a negative `k` raises `ValueError`. |
| `mode` | `RetrievalMode.HYBRID` | Which channels run -- shared with `Retriever`. |

A blank query -- empty, or only whitespace -- raises `ValueError`, matching
`Retriever.retrieve`.

## The three modes

```python
await retriever.retrieve_chunks(q, tenant_id, mode=RetrievalMode.HYBRID)  # both
await retriever.retrieve_chunks(q, tenant_id, mode=RetrievalMode.SEMANTIC)  # vectors only
await retriever.retrieve_chunks(q, tenant_id, mode=RetrievalMode.LEXICAL)  # BM25 only
```

`LEXICAL` makes no embedding call, the same reason it matters on the entity
side: a round trip per query is real cost when the provider is a paid API.

## Reading the scores

`ScoredChunk` carries the same three-number shape as `ScoredEntity`:

- **`score`** is the fused, ordinal rank score -- comparable within one result
  set, meaningless across queries, unbounded.
- **`semantic`** is on `VectorMatch`'s scale: cosine mapped onto `0..1`.
- **`lexical`** is the store's own BM25 scale.

**`None` and `0.0` are different facts**, exactly as for `ScoredEntity`: a
component is `None` when that channel did not rank the chunk at all, and a
float when it did. Do not collapse them with `or 0.0`.

## The two limits to plan around

Both read, to a caller, as a missing result -- not as an error, and not as
anything the store complains about.

**Lexical recall is bounded by the candidate limit passed to
`lexical_candidates`.** The BM25 channel does not rank the whole corpus; it
ranks a truncated candidate set fetched per query term. A chunk matching one
rare, highly informative term can be cut from that set before a chunk
matching two common ones, so a passage that would have ranked first on a full
scan can be **absent from the result entirely**, not merely ranked low. This
is the same shape as the entity side's blocking-key limit, applied to a
term-frequency cutoff instead of a normalized-name prefix.

**A semantic query over a corpus with no embeddings returns nothing, and does
not raise.** `index_documents` only embeds a chunk when you pass it an
`embeddings=` provider; call it without one and every stored chunk has
`embedding is None`. Query that corpus with `mode=RetrievalMode.SEMANTIC` and
`ChunkRetriever` returns an empty result -- there is no per-corpus flag to
check first, because "unembedded" is a fact about each row, not the corpus as
a whole, and refusing the query would mean refusing some rows and not others
mid-answer. A `mode=RetrievalMode.HYBRID` query over the same corpus still
returns its lexical results; only the semantic half goes silent. If a
semantic or hybrid query is coming back thinner than expected, check first
whether the corpus was indexed with an `EmbeddingProvider` at all.

## What a result does not tell you

Nothing here measures whether fusing the two channels beats either one alone
on a real corpus -- the claim is an argument from how each channel fails, not
a measured result, the same gap `Retriever` has on the entity side (B81).

## Related

- [ADR 0038 · The chunk's vector lives on the chunk](../adr/0038-the-chunks-vector-lives-on-the-chunk.md)
- [ADR 0024 · BM25 over the chunk corpus](../adr/0024-bm25-over-the-chunk-corpus.md)
- [ADR 0023 · The chunk corpus](../adr/0023-the-chunk-corpus.md)
- [ADR 0012 · No ANN index in a multi-tenant vector store](../adr/0012-no-ann-index-in-a-multi-tenant-vector-store.md) --
  governs the chunk store's semantic scan too; it is exact and linear in the
  tenant's corpus for the same reason the entity vector store's is.
- [Retrieve entities](retrieve-entities.md) -- the same shape over the entity
  graph.
