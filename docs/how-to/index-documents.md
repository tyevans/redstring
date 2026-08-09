# Index documents without extracting them

You have a pile of documents and no budget to run a model over all of them.
`index_documents` splits them into a `ChunkStore` and asks no model anything,
so the passages are there to search, cite and — later — extract from, at no
per-token cost.

```python
from redstring import InMemoryChunkStore, SourceDocument, index_documents

corpus = InMemoryChunkStore()
report = await index_documents(
    [SourceDocument(id="engine-memo", text=memo_text)],
    store=corpus,
    tenant_id=tenant_id,
)

passages = await corpus.get_by_source("engine-memo", tenant_id)
```

`report` is an `IndexReport`: `documents_indexed`, `chunks_written` and
`documents_skipped`. `get_by_source` returns the passages in `chunk_index`
order, ties broken by id.

There is no `LlmProvider` parameter and no place one could be passed. That is
the point of the function rather than an omission: build the corpus for
everything you hold, then run `build_graph` over whichever subset is worth
paying for.

Swap `InMemoryChunkStore` for `PostgresChunkStore` when the corpus outlives
the process; the port is the same, and the compliance suite in
`src/redstring/testing/chunk_store.py` is what says so. `PostgresChunkStore` is not
in `redstring.__all__` -- reach it by path,
`redstring.chunks.adapters.postgres.PostgresChunkStore`, the way
`Neo4jGraphStore`, `PgVectorStore`, `RedisCache` and `LangChainProvider` are
all reached.

## Choosing how documents are split

`chunker=` takes any `Chunker`. Omitted, you get a `SlidingWindowChunker` with
its own defaults — the same splitter `ExtractionPipeline` uses, so a document
indexed and later extracted is split the same way.

`Chunker` is exported and is the whole contract: a `chunk(text)` returning a
`ChunkingResult`. The bundled sliding-window implementation is reached by path
(`redstring.extraction.chunkers`) rather than exported, so a caller who wants
different window settings is writing against an internal name — which is
recorded as B88 in `BACKLOG.md` and worth knowing before you depend on it.
Your own `Chunker` needs no dotted import at all.

Re-indexing with a different chunker replaces that source's passages
wholesale: the new split is a different chunking, so it is recorded and
`replace_source` deletes the passages the new split does not contain. Chunks
are content-addressed, so passages that survive the re-split keep their ids.

## An empty `entity_ids` means no entities, not "not yet"

Every `StoredChunk` written by this function has an empty `entity_ids`, and
the type says what that means:

> An empty `entity_ids` means no entities were extracted from this passage. It
> does not mean extraction is pending.

There is no third state, and code that reads emptiness as a work queue will be
wrong forever while looking reasonable in review — every passage from this
path is legitimately empty, and so is any passage from extraction that
happened to contain no entities. If you need to know which documents have been
extracted, that question is answered by the event log or by the graph, not by
this field.

## What the default guarantees, and what it does not

`index_documents` takes an optional `event_store`. Without one it creates an
`InMemoryEventStore` per call, and the difference is narrower than "indexing
is idempotent" sounds:

| | repeat within one call | repeat across calls |
|---|---|---|
| no `event_store` | skipped | **re-indexed**, counted as `documents_indexed` |
| `event_store` given | skipped | skipped |

The aggregate refuses a chunking it has already recorded, and that refusal
lives in its *state*. With no event store the second call rebuilds the
aggregate from nothing, so there is no recorded chunking for it to refuse
against.

The consequence is a cost rather than a corruption. Chunks are
content-addressed, so the second write produces the identical rows and the
corpus is unchanged. What is lost is the *report*: a re-run counts every
document as newly indexed, so `documents_indexed` cannot be read as "work that
needed doing".

Pass an `AggregateStore` to make the suppression real, and to have a log the
corpus can be rebuilt from:

```python
report = await index_documents(documents, store=corpus, tenant_id=tenant_id, event_store=events)
```

This is the same trade [`build_graph` makes](use-the-write-model.md).

## Indexing a document you have already extracted discards its entity links

Both write paths emit the same event and both go through `replace_source`,
which writes a source's chunking as one operation. So the last write to a
source wins, whole:

- **Index, then extract.** The extraction's passages carry `entity_ids` and
  land last. The links survive. This is the normal order.
- **Extract, then index.** The indexing's passages carry no `entity_ids` and
  land last. **The links are discarded.**

That is documented behaviour with a test pinning it, not an accident — but it
is lossy, and nothing warns you. Re-extract the document if you need the links
back. The safe habit is to index a corpus once, up front, and let extraction
be the thing that runs afterwards.

The two paths are deliberately *not* deduplicated against each other: they
record their chunkings under different keys, because a scheme that called them
the same chunking would make indexing-before-extracting silently emit nothing
and drop every entity link the extraction found, while reporting success.

## Related

- [Use the write model](use-the-write-model.md) — appending events yourself
  and driving projections from a log.
- [Implement a store adapter](implement-a-store-adapter.md) — writing a
  `ChunkStore` of your own against the shared compliance suite.
- [ADR 0023 · The chunk corpus](../adr/0023-the-chunk-corpus.md) — why chunk
  ids are content-addressed and why `replace_source` is one call.
