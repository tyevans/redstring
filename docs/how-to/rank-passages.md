# Rank passages

Once documents are chunked into a `ChunkStore` -- see
[Index documents without extracting them](index-documents.md) -- you can rank
the stored passages against a query with BM25, a real term-weighted ranker
over the chunk corpus.

Everything here is in `redstring.__all__`, so nothing in this guide reaches
past the public API.

**This is not the entity lexical channel.** `Retriever` (see
[Retrieve entities](retrieve-entities.md)) also has something it calls
"lexical", and it is a field-weighted string similarity over entity names --
not BM25, and not a replacement for what is here. This ranker scores stored
*passages*, not entity names, and answers "which chunks discuss this", not
"which entity matches this string". Read
[ADR 0022](../adr/0022-the-lexical-channel-is-not-bm25.md) for why those are
different channels, and [ADR 0024](../adr/0024-bm25-over-the-chunk-corpus.md)
for why this one is allowed to use the name.

## Rank

```python
import asyncio
from uuid import uuid4

from redstring import (
    InMemoryChunkStore,
    SourceDocument,
    index_documents,
    rank_chunks,
    tokenize,
)


async def main() -> None:
    tenant_id = uuid4()
    chunks = InMemoryChunkStore(dimension=768)

    await index_documents(
        [
            SourceDocument(id="notes", text="Ada Lovelace worked with Charles Babbage."),
            SourceDocument(id="weather", text="It rained in London on Tuesday."),
        ],
        store=chunks,
        tenant_id=tenant_id,
    )

    terms = tokenize("Ada Lovelace")
    candidates = await chunks.lexical_candidates(terms, tenant_id, limit=20)
    ranked = rank_chunks(terms, candidates, k=5)

    for result in ranked:
        print(f"{result.score:.4f}  {result.chunk.text}")


asyncio.run(main())
```

Two calls do the work: `ChunkStore.lexical_candidates` asks the adapter for
candidate chunks and the corpus statistics BM25 needs, and `rank_chunks` --
pure, and identical over either adapter -- scores and orders them.

- **`tokenize`** decides what counts as a term: NFKC-normalised, casefolded,
  alphanumeric runs, with a small English stopword list dropped. Both
  adapters tokenize through this one function, which is what lets the
  compliance suite assert they rank identically. There is no stemming --
  `"running"` does not match a query for `"run"` -- see
  [ADR 0024](../adr/0024-bm25-over-the-chunk-corpus.md) for the cost and why
  it is deferred rather than built.
- **`rank_chunks(terms, candidates, k)`** returns the best `k` `RankedChunk`s,
  score descending, ties on `chunk.id` ascending. A chunk matching none of
  `terms` is dropped rather than returned with a zero score.

## `RankedChunk.score` is unbounded and ordinal

Like `ScoredEntity.score` on the entity channel, a BM25 score is comparable
only within one result set. It is not on `0..1`, it is not a probability, and
it is not comparable across queries or corpora. Use it to order the chunks
one query returned, not to threshold "good enough" across different queries.

## The cost of `limit`: bounded recall, the same shape as blocking

**`lexical_candidates(terms, tenant_id, limit)` truncates before `rank_chunks`
ever runs**, and the truncation is a stated total order, not a random or
arbitrary cut: candidates are ordered by how many *distinct* requested terms
they match, then by chunk id, and only the first `limit` are returned as
candidates at all.

The consequence is the same shape as ADR 0022's blocking limit on entity
retrieval, and it is worth stating here for the same reason that one is
stated in [Retrieve entities](retrieve-entities.md): **a missing result reads
as a bug rather than as a declared limit.**

Concretely: a chunk matching one rare, highly informative term can be cut
from the candidate set before a chunk matching two common terms, even though
BM25's IDF weighting would have scored the rare-term chunk higher. Distinct
term count is decided *before* any BM25 weighting is applied, because
weighting needs corpus-wide statistics -- computed once, over every document
in the tenant's corpus, never over the candidate set truncation leaves behind
-- and truncating by distinct-term-match count first is what bounds the
candidate set without requiring a scan of the whole corpus to rank it. A
passage that would have ranked first among
all your chunks can therefore be **absent from the ranked results entirely**,
not merely ranked low, if it happens to share fewer distinct terms with the
query than `limit` other chunks do.

Raise `limit` if recall matters more than the cost of a wider candidate scan;
there is no way to have both an exact top-`k` and a bounded scan over an
unindexed corpus.

## Related

- [ADR 0024](../adr/0024-bm25-over-the-chunk-corpus.md) -- why the scorer is
  pure and the tokenizer is domain-owned, and what truncation costs.
- [ADR 0023](../adr/0023-the-chunk-corpus.md) -- how the chunk corpus is
  built and identified.
- [ADR 0022](../adr/0022-the-lexical-channel-is-not-bm25.md) -- why the
  entity lexical channel is not this, and why blocking bounds its recall the
  same way `limit` bounds this one's.
- [Index documents without extracting them](index-documents.md) -- building
  the corpus this ranks over.
- [Retrieve entities](retrieve-entities.md) -- the entity-level channels,
  fused by rank rather than ranked by BM25.
