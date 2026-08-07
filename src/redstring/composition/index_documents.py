"""Chunking a document into the corpus, with no model call anywhere.

```
SourceDocument -> Chunker -> Document.record_chunking
               -> DocumentChunked -> ChunkProjection -> ChunkStore
```

## Why this belongs in `composition`

The layer's rule is that a module names the pair of mutually-forbidden layers
it joins. This one joins `extraction` (the chunkers) and `projections` -- the
same pair `build_graph` names, so the rule is satisfied by the argument
already recorded in `pyproject.toml` rather than needing a new one. A chunker
may not import a store, and a projection may not import a chunker; somebody
has to hold both.

## The signature is deliberately *not* the pipeline's

This path emits `f"{method}:{digest}"`; the extraction pipeline emits
`f"{method}:{digest}:{model_version}"`. They are different key spaces on
purpose, and the asymmetry is the whole point:

- **Index, then extract** -- two different signatures, so the extraction is
  recorded too, and its chunks (which carry `entity_ids`) land last and win.
  The links survive.
- **Extract, then index** -- also two different signatures, so the indexing is
  recorded, `replace_source` replaces the whole source, and the entity links
  are **discarded**. That is documented behaviour rather than an accident, and
  `test_indexing_after_extracting_discards_the_entity_links` pins it so that
  changing it is a visible decision.

Making the two signatures equal would be worse in a way that is silent: the
second write would read as a repeat and emit nothing, so indexing a document
before extracting it would drop every entity link the extraction found and
report success.

## Idempotence needs a log, and by default there is not one

`Document.record_chunking` refuses a signature the aggregate has already
recorded, and that refusal lives in the aggregate's *state*. With no
`event_store` this function creates an in-memory one per call, so:

- **within one call**, a document listed twice is indexed once;
- **across calls**, it is not -- the second call rebuilds the aggregate from
  nothing and re-emits.

Re-emitting is harmless to the corpus (the chunks are content-addressed, so
`replace_source` writes the identical rows) and visible in the report, which
counts it as indexed rather than skipped. Pass an `event_store` to make the
suppression real and to have a log the corpus can be rebuilt from. This is
the same trade `build_graph` makes, and it is stated here rather than
discovered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from eventsource.adapters.memory import InMemoryEventStore
from eventsource.domain.tenant_context import tenant_scope

from redstring.aggregates.repositories import document_repository
from redstring.events.streams import document_stream
from redstring.extraction.chunkers import SlidingWindowChunker
from redstring.extraction.corpus import chunking_digest, stored_chunks
from redstring.projections.chunk import ChunkProjection

if TYPE_CHECKING:
    from collections.abc import Sequence

    from eventsource.ports.store import AggregateStore

    from redstring.domain.ids import TenantId
    from redstring.domain.source import SourceDocument
    from redstring.extraction.protocols import Chunker
    from redstring.ports.chunk_store import ChunkStore


@dataclass(frozen=True, slots=True)
class IndexReport:
    """What one `index_documents` call split, and what it wrote."""

    #: Documents whose chunking was recorded. Excludes repeats.
    documents_indexed: int
    #: Passages written to the `ChunkStore`. Larger than
    #: `documents_indexed` for any document that split, and equal to it only
    #: when every document fitted in one chunk -- so a report wiring both
    #: fields to one count is not distinguishable on a corpus of short
    #: documents. `test_the_report_counts_documents_and_chunks_separately`
    #: uses documents that split.
    chunks_written: int
    #: Documents already chunked under this signature, so nothing was
    #: emitted for them. Zero without an `event_store`, except for a document
    #: listed twice in one call. Not a failure count -- a repeat is the
    #: expected outcome of re-running an index over a corpus that has not
    #: changed.
    documents_skipped: int


async def index_documents(
    documents: Sequence[SourceDocument],
    *,
    store: ChunkStore,
    tenant_id: TenantId,
    chunker: Chunker | None = None,
    event_store: AggregateStore | None = None,
) -> IndexReport:
    """Split `documents` into `store`, without asking a model anything.

    Args:
        documents: The content. Supplied by the caller -- this library never
            fetches anything.
        store: The corpus. Any `ChunkStore`.
        tenant_id: Applied to every passage and every store call.
        chunker: How to split. A `SlidingWindowChunker` with its own defaults
            when None, matching `ExtractionPipeline`.
        event_store: Where the chunkings are recorded. An `InMemoryEventStore`
            per call when omitted -- see the module docstring on what that
            costs idempotence.

    Returns:
        An `IndexReport`.

    There is no `LlmProvider` parameter and no place one could be passed. That
    is the point of this function: a corpus can be built for every document a
    caller holds, at no per-token cost, and extraction can then be run over
    whichever subset is worth paying for. The passages written here carry no
    `entity_ids`, which `StoredChunk` documents as "no entities were extracted
    from this passage" and explicitly not as "extraction is pending".
    """
    splitter = chunker if chunker is not None else SlidingWindowChunker()
    repository = document_repository(
        event_store if event_store is not None else InMemoryEventStore()
    )
    projection = ChunkProjection(store)

    indexed = 0
    written = 0
    skipped = 0

    async with tenant_scope(tenant_id):
        for document in documents:
            chunking = splitter.chunk(document.text)
            aggregate = await repository.load_or_create(
                document_stream(tenant_id=tenant_id, source_id=document.id).aggregate_id
            )
            event = aggregate.record_chunking(
                tenant_id=tenant_id,
                source_id=document.id,
                chunking_signature=f"{splitter.chunker_type}:{chunking_digest(chunking)}",
                chunks=stored_chunks(chunking, tenant_id=tenant_id, source_id=document.id),
            )
            if event is None:
                skipped += 1
                continue
            # Saved before the projection runs. The log is the authority: a
            # crash between the two leaves an event that a replay will apply,
            # whereas projecting first and crashing leaves a corpus holding
            # passages no log accounts for.
            await repository.save(aggregate)
            await projection.handle(event)
            indexed += 1
            written += len(event.chunks)

    return IndexReport(documents_indexed=indexed, chunks_written=written, documents_skipped=skipped)
