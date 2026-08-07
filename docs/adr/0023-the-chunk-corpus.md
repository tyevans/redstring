# ADR 0023: The chunk corpus, and what a stored passage knows about the graph

## Status

Accepted.
[`0022` the lexical channel is not BM25](0022-the-lexical-channel-is-not-bm25.md)
is **amended**: its premise that this library stores no text no longer holds.
Its *decision* stands unchanged — the lexical channel is still a
field-weighted string similarity and still not a term-weighted ranker, and a
ranker over passages would not replace the thing that catches `Acme Corp`.
0022 anticipated this document by name and deferred to it.

[`0001` event log schema and granularity](0001-event-log-schema-and-granularity.md)
**stands.** `DocumentChunked` is a new event at document granularity on the
aggregate that already owns document facts, which is the granularity 0001
settled rather than an exception to it. A whole document's chunking in one
event is what makes the fold a single atomic call.

[`0002` two store ports](0002-two-store-ports.md) **stands**, and is not
superseded by a third port. 0002 is about the *graph and vector pair* — why
those two are separate rather than one, and why neither has `delete_entity`.
A chunk store is orthogonal to both questions: it does not hold entities, so
the `delete_entity` argument does not arise, and the reason the pair are
separate ports is the reason this is a third one rather than a method on
either. What 0002 decided about the graph/vector boundary is untouched. The
sentence "there are exactly two store ports" was a count, not a decision, and
0002 does not make it.

[`0007` `composition` is the only top layer](0007-composition-is-the-only-top-layer.md)
and
[`0021` `composition` holds a second module](0021-composition-holds-a-second-module.md)
— **0021 governs**, and it stands. 0007's Decision 1 ("the layer holds exactly
one module") was already amended in fact by 0021; what survives from 0007 and
is what a third module is judged against is its *admission test*: name the pair
of mutually-forbidden layers this module joins. `index_documents` joins
`extraction` (the chunkers) and `projections` (the store side) — the same pair
`build_graph` names, so it is admitted on an argument already recorded rather
than a new one. A chunker may not import a store and a projection may not
import a chunker; something has to hold both.

[`0006` the public surface is gated](0006-the-public-surface-is-gated.md)
**stands.** The new exports went through the same three gates, and the
signature gate did what it exists to do: it named the closure as each piece
landed rather than leaving it to review.

## Context

`SlidingWindowChunker` split a document, handed the pieces to a model, and
discarded them. Two consequences followed, and 0022 named both while deferring
both: nothing downstream of extraction knew *which passage* stated a fact, and
there was no corpus over which any term statistic could be computed.

Building the corpus first, and the ranker later, is deliberate. Every decision
a search method would encode is downstream of what a stored passage *is*, and
a port that acquires a method its adapters cannot implement identically is the
expensive mistake. The port therefore ships with no search method at all.

## Decision

### "Never fetches" and "never stores" are separated, and only the first was a decision

The library has two rules that were previously stated as though they were one:

- **It never fetches content.** A caller supplies every byte. This is a
  decision, it is load-bearing, and it is unchanged.
- **Extraction writes to no store.** It emits events; projections write. Also
  a decision, also unchanged.

"This library stores no text" was neither. It was an accurate description of
what had been built, and it acquired the authority of a principle by sitting
in an argument beside two real ones. Retaining a caller-supplied passage
violates neither rule. Separating the three is the first thing this decision
does, because otherwise the corpus reads as a reversal rather than as a
capability the stated principles always permitted.

### Chunk identity is content-addressed

A chunk's id is a digest over `(source_id, text)`, with the text exactly as
stored and no normalisation. The source id is part of it, so identical
boilerplate under two documents is two passages rather than one row the two
documents fight over on every replay.

**Positional identity — `(source_id, chunk_index)` — was rejected.** It is
simpler, and it makes re-chunking an in-place overwrite, which is precisely
the defect: chunk 3 of a re-chunked document is a *different passage* wearing
the same id, so its stored entity links, and later its stored embedding, would
silently describe text that no longer says what they claim. Content addressing
makes a re-chunk produce new ids and leaves the old rows wrong-but-identifiable
rather than lying. The cost is orphans, and it is paid in the port.

Normalising the text before hashing was rejected for a second reason: it would
create an identity scheme that has to be kept in step with the one in
`extraction/mapping.py`, and two normalisation schemes that drift is the hazard
that keeps `consolidation` a sibling layer rather than a consumer of
extraction.

### `replace_source` is one operation

The port replaces a source's chunking in a single call: write the incoming
passages, delete that source's passages absent from them. An empty incoming
set is legal and empties the source; it is not a no-op guard.

**The split version — an `upsert_many` followed by a delete — was rejected.**
A crash between the two leaves a corpus that is neither the old chunking nor
the new one, and once a term-weighted ranker exists it leaves document
statistics computed over a set that never existed. Folding one
`DocumentChunked` is therefore one call, which is also why the projection's
handler is a single line.

### Entity links live on the chunk, not in the graph

`StoredChunk.entity_ids` points from a passage to what was extracted from it,
and the graph holds no chunk reference. A join across the two ports is the
caller's business. Putting a chunk id into the graph would give `mapping.py` a
second id scheme to keep in step.

**An empty `entity_ids` means no entities were extracted from this passage. It
does not mean extraction is pending.** There is no third state and no queue.
This has to be stated on the type, because an entire class of passages — every
one arriving through direct ingest, which never calls a model — is legitimately
empty forever, and code reading emptiness as "not yet processed" will look
reasonable in review.

### A chunking signature digests the split produced, not the chunker's settings

The aggregate records what chunking it has already seen, so a repeat emits
nothing. The plan called that record's middle field a digest of chunker
*parameters*. That is unimplementable here and would be wrong if it were not:
`Chunker` exposes `chunker_type` and nothing else, `ChunkingResult` carries
`overlap_size` but not the chunk size, and the sliding-window chunker zeroes
`overlap_size` on its single-chunk path — so a digest over what a chunker
reported about itself would call two different chunk sizes the same chunking,
which is exactly the case that re-indexing with different settings turns on.

The signature therefore digests the boundaries and text of the split that was
actually produced. It is stronger in the direction that matters — settings that
happen to produce an identical split really are the same chunking — and the
cost is that the signature cannot be computed without chunking first, which no
caller wanted to avoid.

**`params_digest` is recorded here as the rejected alternative**, so that
someone reading the spec and then the code does not read the substitution as
drift.

### The two write paths compose the signature differently, on purpose

Extraction appends its model version to the signature; direct ingest does not.
The two therefore occupy different key spaces, and neither suppresses the
other:

- **Index, then extract** — different signatures, so the extraction is
  recorded, and its passages (which carry `entity_ids`) land last and win.
- **Extract, then index** — different signatures, so the indexing is recorded,
  `replace_source` replaces the whole source, and the entity links are
  **discarded**.

Making the two signatures equal would be worse in a way that is silent: the
second write would read as a repeat and emit nothing, so indexing a document
before extracting it would drop every entity link the extraction found and
report success.

## Consequences

**A corpus now exists, so a term-weighted ranker over it is possible.** This
work does not build one, and does not build chunk embeddings either. That is
the second half of the plan, and its decisions are downstream of the port
shipping without a search method — which is the point of shipping it that way.

**The entire correctness of the two key spaces rests on the model-version
suffix.** `entity_ids` is outside the digest — it has to be, because the digest
is computed from the split before any model has run — so nothing about the
passages themselves distinguishes an indexed chunking from an extracted one.
The suffix extraction appends is the only thing that does. This is the fragile
part of the design and the one a future simplification will reach for first: a
reader tidying two spellings into one symmetric key would be removing the whole
mechanism, and the code would keep working for every test that indexes or
extracts but not both. It is pinned by two behavioural tests over the resulting
corpus rather than by a string assertion, because a test that asserts the
*format* of the signature would be satisfied by any two formats and would move
with the next refactor.

**The aggregate's refusal is redundant, not vacuous, and the distinction
matters.** The signature and the content-addressed chunk ids derive from
overlapping data, and the digest strictly refines the id set — it adds each
chunk's index and offsets — so an equal digest implies an identical set of
rows, which `replace_source` would have written idempotently anyway. The
refusal therefore saves an event write; it does not establish correctness.
Reading it as the thing that makes re-indexing safe would be the wrong model,
because it would suggest the idempotence weakens when no event store is passed,
and it does not: without one the refusal is simply absent and the corpus is
unchanged regardless.

**An empty chunking digests to SHA-256 of the empty string, which is the same
value for every document.** That is safe only because a chunking signature is
scoped to one document's aggregate and is never compared across documents. If
signatures ever become global — an index of chunkings, a cross-document
dedupe — every empty document collides at once. Whoever proposes that has to
solve this first; it is not a latent bug today.

**Passing `event_store` alone, without `chunks`, still writes document text
into the log.** `record_chunking` runs on the aggregate whenever `event_store`
is given, independently of whether `chunks` is; only the projection into the
corpus is gated on `chunks`. A caller who passed `event_store` only for
extraction idempotence gets a `DocumentChunked` carrying the document's full
text in every run from then on. This is intentional -- the log has to hold the
event regardless of whether anything projects it today, or a corpus built
later by replay would be missing chunkings that predate it -- but it means
"never fetches" is not "never logs": `event_store` moves document text into
the caller's log even when `chunks` says "do not maintain a corpus".

**`build_graph` gained an optional `event_store`.** Without it the two write
paths could not share aggregate state, and the key-space behaviour above was
untestable: a test that extracts and then indexes needs both paths loading the
same aggregate. It is optional, so the no-log composition 0007 argued for is
unchanged, and the trade is the same one `index_documents` states — without a
log, a repeat across calls is not suppressed and the report over-counts, while
the corpus is identical either way.

**A caller can build a corpus without paying for a model.** `index_documents`
has no `LlmProvider` parameter and no place one could be passed, which makes
"index everything, extract what is worth it" the cheap default rather than a
pattern a caller has to assemble.
