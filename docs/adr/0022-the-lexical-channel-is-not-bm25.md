# ADR 0022: The lexical channel is not BM25, and its recall is bounded by blocking

## Status

Accepted. [`0002` two store ports](0002-two-store-ports.md) stands — retrieval
reads through the existing ports and adds no method to either.
[`0003` blocking keys as nodes](0003-blocking-keys-as-nodes.md) stands, and
this decision leans on it: the same keys that make consolidation affordable
are what a query looks for candidates under.
[`0012` no ANN index in a multi-tenant vector store](0012-no-ann-index-in-a-multi-tenant-vector-store.md)
stands and is why the semantic channel is exact.

Amended by [`0023` the chunk corpus](0023-the-chunk-corpus.md). The premise
"this library stores no text" no longer holds — a `ChunkStore` retains the
passages a document was split into. The **decision below stands unchanged**:
the lexical channel is still a field-weighted string similarity over entity
names and still not a term-weighted ranker, and a ranker over passages would
be a different and additional thing rather than a replacement for the one that
catches `Acme Corp`.

Amended a second time by
[`0024` BM25 over the chunk corpus](0024-bm25-over-the-chunk-corpus.md). The
sentence below reading "the name 'BM25' appears nowhere under `src/`" no
longer holds — `domain/bm25.py` is a real term-weighted ranker over the real
document corpus 0023 built, and the name is honest there for the first time.
The **decision below stands unchanged** for the channel this document is
about: the entity lexical channel is still a field-weighted string similarity,
still not BM25, and 0024's ranker does not replace it.

## Context

A retrieval surface over entities wants two channels, because they fail in
different places. Cosine over embeddings catches paraphrase and misses exact
strings; string similarity catches `ACME Corporation` against `Acme Corp` and
misses everything that does not look alike.

The obvious name for the second channel is BM25, and it is the wrong name.
BM25 weights a term by corpus statistics — inverse document frequency, and a
length normalisation against the average document length. Both quantities are
defined over a corpus of *documents*. This library stores entity names, and an
entity name is three words. Term frequency within one is almost always 1,
document length barely varies, and IDF over a few thousand names measures how
common a surname is rather than how informative a term is. The formula would
still compute; the number would not mean what its name claims.

There is also nothing to run it on. **This library stores no text.** An
`Entity` carries a name, a `normalized_name`, and free-form properties. The
source document is not retained, so there is no document collection to gather
statistics over even if the statistics were meaningful.

## Decision

**The lexical channel is a field-weighted string similarity, and it is not
named after a term-weighted ranker.** `domain/lexical.py` scores the best of
the name, the extractor's `normalized_name`, and each string property at
`PROPERTY_WEIGHT`. Maximum over fields, never sum: a sum would let many
mediocre fields outrank an exact name match and would leave the score
unbounded above.

**The name "BM25" appears nowhere under `src/`** — not as a module, a class, a
function, or a docstring aside. The term belongs in this document, where the
argument for not using it lives.

**Candidate generation reuses blocking keys.** `query_blocking_keys(query)`
derives a prefix key and a soundex key from the query string and asks
`GraphStore.find_by_blocking_keys` for the entities carrying them. The entity
*type* key is deliberately excluded: it matches every entity of a type, so
including it would turn candidate generation into a full scan the moment a
query happened to share one. `entity_types` filters the candidates instead.

**The two channels are fused by rank, not by score.** `RRF_K = 60` is a module
constant, not a parameter.

## Consequences

**Lexical recall is bounded by blocking, and this is the real cost.** A query
sharing no blocking key with an entity cannot be retrieved lexically, however
high its string similarity would have been. A query is blocked on its first
five normalized characters and on the soundex of its whole name, so a query
matching an entity only in its *last* word — "Lovelace" against "Ada
Lovelace" — is not a lexical candidate at all. The semantic channel is the
only thing covering that case, and against a real embedding model it usually
does.

This has to be said in the caller's documentation, not only here, because a
missing result reads as a bug rather than as a stated limit. It is in
`docs/how-to/retrieve-entities.md` and in the `Retriever` module docstring.

**Nothing measures whether the hybrid actually beats the semantic channel
alone.** The claim that fusion helps is an argument from how the two channels
fail, not a result: there is no graded retrieval corpus in this repository,
and the in-gate tests use a hash-based fake provider whose vectors carry no
semantics. Filed as B81, together with B80 — `PROPERTY_WEIGHT = 0.6` is a
judgement that the same corpus would settle.

### Rejected: a weighted blend of the two scores

Both channels emit numbers on `0..1`, so `0.7 * semantic + 0.3 * lexical`
looks available. It is not: the shared range is a coincidence of both being
normalised, and the two have **no common unit**. A weighted sum invents an
exchange rate between "cosine similarity of two embeddings" and "Jaro-Winkler
edit proximity of two strings" — a rate that will be wrong for some corpus and
that nothing in this repository could falsify for any corpus.

Reciprocal rank fusion uses only position, which is the one thing both
channels genuinely produce. Its cost is real and stated where it is paid: RRF
discards magnitude, so a semantic match at 0.99 and one at 0.51 contribute
equally if both rank first. That is why `ScoredEntity` retains both component
scores — the caller can see what fusion threw away, and a `None` there means
the channel did not rank the entity rather than that it scored zero.

### Rejected: calling it `bm25` anyway

Tempting because it names the *role* — "the lexical channel" — in a word every
reader recognises. Rejected because a name that describes an algorithm the
code does not implement is a claim, and this one would be checked: a caller
who reads `bm25` will reasonably expect IDF weighting, and will tune, debug
and file bugs against a model of the code that is false. The cost of the
honest name is one sentence of explanation. The cost of the familiar one is
paid by whoever debugs a ranking they think they understand.

### Deferred: a chunk store, and then a real term-weighted ranker

Real BM25 needs stored text. If a chunk store lands — retaining the source
passages an entity was extracted from — the statistics become meaningful over
*those*, and a term-weighted ranker over chunks is a different and better
thing than one over names. That is a separate decision with its own ADR, and
it does not retroactively make this channel a bad one: name matching is what
catches `Acme Corp`, and a chunk ranker would not replace it.
