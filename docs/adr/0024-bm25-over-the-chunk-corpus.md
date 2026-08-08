# ADR 0024: BM25 over the chunk corpus, scored in the domain

## Status

Accepted. [`0012` no ANN index in a multi-tenant vector store](0012-no-ann-index-in-a-multi-tenant-vector-store.md)
refused the same trade for the semantic channel and is the precedent this
decision follows for the lexical one. [`0022` the lexical channel is not
BM25](0022-the-lexical-channel-is-not-bm25.md) is amended a second time, in
its Status only: the sentence *"the name 'BM25' appears nowhere under
`src/`"* no longer holds. [`0023` the chunk corpus](0023-the-chunk-corpus.md)
stands, and this document is the "later" it deferred to.

## Context

0022 refused to call the entity lexical channel BM25 because there was
nothing to run it on -- no stored text, so no document collection to gather
statistics over. 0023 built the corpus and stopped there deliberately: "every
decision a search method would encode is downstream of what a stored passage
*is*", and shipping the wrong method costs an adapter divergence in a way
shipping none does not.

The corpus now exists. `ChunkStore` has two adapters, `InMemoryChunkStore`
and `PostgresChunkStore`, and both need to answer the same ranking query the
same way -- the compliance suite in `tests/compliance` is what would notice
if they did not.

## Decision

### Scoring is a pure function in `domain/`; recall and statistics are the adapter's job

`domain/bm25.py::bm25_score` takes term frequencies, a document length, and
`CorpusStats`, and returns a number. It imports nothing store-shaped and
knows nothing about a query or a tenant. `domain/chunk_ranking.py::rank_chunks`
calls it over a `LexicalCandidates` a store produced and returns
`RankedChunk`s, ordered.

**Rejected: `ts_rank_cd` in Postgres, with the in-memory adapter approximating it.**
Postgres has a text-search ranking function built in, and using it would mean
writing less code. It is rejected for the reason 0022 already established for
tokenization and 0023 established for chunk identity: two adapters computing
a rank by two different formulas can *agree on which chunks match* and still
disagree on their order, and nothing would notice except a caller comparing
results across backends. Putting the arithmetic in one place the compliance
suite can call directly against both adapters' candidate sets is what makes
"the two adapters rank identically" a thing that gets asserted rather than
hoped for. The adapters' job is narrower and does not admit the same
divergence: hand back which chunks contain which terms, and how many, and
`n_docs` / `avg_doc_length` / `doc_frequencies` for the corpus. Those are
counts, not rankings, and two adapters counting the same rows cannot disagree.

### The tokenizer is domain-owned, or the purity of the scorer buys nothing

`domain/tokenize.py::tokenize` is the one function that decides what a term
is, called by both adapters to build their term index and by a caller to
build a query's term list. If it were not shared -- if, say, Postgres used its
built-in `english` text-search configuration and the in-memory adapter
approximated it in Python -- every count `CorpusStats` carries would already
have diverged before `bm25_score` ever ran, and a pure scorer over
inconsistent inputs is not a pure system. Tokenization is upstream of every
number BM25 computes, which is why it is the one piece of this design that
had to be decided first (Task 1) rather than last.

### No stemming, and the cost is stated where it is paid

`tokenize` does not stem. `"running"` and `"run"` are different terms to it,
and a query for one does not match a passage containing only the other. The
alternative -- a stemmer -- is a language model: English-only, and a new
dependency, and "the Porter stemmer" is not one algorithm but a family of
slightly different ones, so two implementations (a Postgres extension and a
Python library, say) would reintroduce exactly the divergence a shared
tokenizer exists to prevent, one level up. Deferred as a single domain-owned
implementation if it is ever added -- BACKLOG B91, filed by Task 1, restated
here because this is where the cost is paid in practice: a real corpus, most
of the time, means a real recall gap from every unstemmed query.

### Truncation is a stated total order, and its cost is bounded recall

`lexical_candidates(terms, tenant_id, limit)` cannot return every chunk that
matches any term -- that is a full scan of the corpus per query, the same
cost 0012 refused for the vector store's exact search and 0022 refused by
routing entity candidates through blocking keys instead. It truncates before
`rank_chunks` ever sees the candidates, ordered by **distinct matched term
count, then chunk id** -- computed before BM25 weighting, because the
weighting needs corpus statistics computed *over* the candidate set, and
computing those first would mean scanning the whole corpus regardless of
`limit`.

The consequence is the same shape 0022 already named for blocking: a chunk
matching one rare, highly informative term can be truncated away before a
chunk matching two common ones, even though IDF weighting would have scored
the rare-term match higher once both were candidates. A passage that would
have ranked first among all stored chunks can be **absent from the ranked
results entirely**, not merely low-ranked. `docs/how-to/rank-passages.md`
states this in the caller's own documentation, for the reason 0022 states its
blocking limit in `docs/how-to/retrieve-entities.md`: a missing result reads
as a bug rather than as a declared limit, unless the caller has been told.

### `ON DELETE CASCADE` maintains the term index, because content addressing makes it immutable per id

`PostgresChunkStore`'s `<table>_terms` table is a foreign key on
`(tenant_id, chunk_id)` back to the chunk table, `ON DELETE CASCADE`. Every
path that removes a chunk -- `replace_source`'s orphan delete, `delete_by_source`,
`delete_by_tenant` -- deletes term rows for free, without any of the three
needing to know the term table exists. This is only safe because 0023 made
chunk ids content-addressed over `(source_id, text)`: a chunk's term counts
are a pure function of its `text`, and a content-addressed id fixes that text
for good, so the term index for a given id is written once and never updated
in place. If chunk ids were positional instead, an in-place re-chunk would
need to *update* term rows rather than let a delete clean them up, and the
cascade would not be sufficient on its own.

### The name "BM25" is honest here, for the first time in this codebase

0022 said the name would be wrong for the entity lexical channel because
there was no document collection and no meaningful corpus statistics to
compute — a three-word entity name has no length to normalise and no
informative document frequency. Neither objection applies to a corpus of
chunked passages: `n_docs`, `avg_doc_length` and `doc_frequencies` are
measured over real documents of real, varying length, and the formula in
`domain/bm25.py` is unmodified Robertson/Sparck-Jones IDF with the standard
saturation and length normalisation. **This is a real term-weighted ranker
over a real document corpus, and it is the first time the name has meant what
it claims under `src/`.**

0022's decision about the *entity* channel is untouched by this. It is still
a field-weighted string similarity over names and properties, it is still not
to be called BM25, and this ranker does not replace it: `Acme Corp` against
`ACME Corporation` is a string-proximity match this ranker has no way to make,
because a corporate name that short carries almost no term-frequency signal
distinguishing it from any other three-word string. Two channels this
document did not confuse is why both remain legible; a caller reaching for
"the lexical channel" now has to say which one.

### 0023's "no search method" was about timing, and the condition it named is met

0023 shipped `ChunkStore` with no search method because "every decision a
search method would encode is downstream of what a stored passage *is*", and
answering that with no corpus to test against would have been guessing. The
corpus has since been built, proven against real Postgres (Task 7), and this
document is the search method built once there was something to build it
against. 0023 is not overridden here -- the condition its argument named as
the reason to wait has been satisfied, which is a different thing from the
argument having been wrong.

## Consequences

**A caller can rank passages without a model call.** Like `index_documents`,
`lexical_candidates` and `rank_chunks` ask nothing of an `LlmProvider` or an
`EmbeddingProvider` -- ranking a corpus is as cheap as indexing one.

**Chunk embeddings and a fused `retrieve_chunks` entry point still do not
exist.** This document builds the lexical half of what BACKLOG's now-former
B89 (B2) called for; the semantic half, `ScoredChunk`, and fusion with RRF
the way `Retriever` fuses entity channels are unbuilt and unscheduled.

**Corpus statistics are recomputed per query rather than maintained
incrementally.** `n_docs`, `avg_doc_length` and document frequencies are
counted at query time (`count(*)`, `avg()`, per-term counts scoped to the
requested terms) rather than cached and kept in step with writes. This is
adequate at the scale this repository has measured anything at, and building
counters that are updated on every `upsert_many` / `replace_source` /
`delete_by_source` would be exactly the kind of speculative cost BACKLOG
warns against building ahead of a measured need.
