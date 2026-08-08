# B2a: a term-weighted ranker over the chunk corpus

**Status:** design, approved for planning.

**Goal.** Rank stored passages by BM25 — a real term-weighted ranker over a
real document corpus, which is the thing ADR 0022 said this library could not
have and ADR 0023 built the corpus for.

**Not in this work.** Chunk embeddings, `ScoredChunk`, `retrieve_chunks`, and
fusion. Those are B2b, and they are deliberately downstream: a public result
type carrying two component scores cannot be designed correctly until both
components exist. B2a therefore ships a complete, tested ranking capability
and no public retrieval entry point — the same shape B1 shipped a corpus with
no search method, and for the same reason.

## Context

`ChunkStore` has held passages since B1 and has no search method, on purpose.
ADR 0023: *"Every decision a search method would encode is downstream of what
a stored passage is, and a port that acquires a method its adapters cannot
implement identically is the expensive mistake."* The corpus now exists, so
the decision can be made against it.

ADR 0022 refused to call the entity lexical channel BM25 on two grounds: an
entity name is three words, so term statistics over names measure how common a
surname is; and there was no text to compute statistics over. The second
ground is gone. The first is untouched — this work adds a ranker over
*passages*, beside the string similarity over *names*, and does not replace
it.

## Decision

### Relevance is a domain rule, so scoring happens in `domain/`

The obvious implementation is `ts_rank_cd` over a Postgres `tsvector`, and it
is rejected. Two adapters ranking by different formulas means retrieval
quality changes when a caller swaps their store, and the compliance suite —
this repository's strongest gate, the thing that has caught every adapter
divergence so far — could no longer assert that two adapters agree. It would
be reduced to asserting contracts while the answers diverged silently. ADR
0012 already refused exactly this trade for the semantic channel: no ANN
index, the scan is exact.

The split is by responsibility:

- **The adapter owns recall and corpus statistics.** Which chunks contain a
  query term, how many chunks a term appears in, how long the average chunk
  is. Every one of these is a storage question, and a database is uniquely
  good at all of them.
- **The domain owns scoring.** Given term frequencies, a document length, and
  corpus statistics, BM25 is a pure function of numbers. It imports nothing
  and touches no store.

Consequence: both adapters produce **identical rankings for identical
corpora**, and the compliance suite asserts that directly.

### The tokenizer is domain-owned, and the term index is stored

If Postgres tokenized with its `english` configuration while the in-memory
adapter split on whitespace, the two would disagree about what a term *is*,
and the identical-ranking property above would be false no matter how pure the
scorer was. Tokenization is therefore `domain/tokenize.py`, and adapters store
the terms it produces.

`tokenize(text) -> list[str]`: NFKC-normalise, casefold, split on Unicode
non-alphanumerics, drop a small built-in stopword set, drop empties. Order is
preserved and repeats are kept — the caller counts them.

**No stemming.** A stemmer is a language model: it is English-only, it is a
dependency, and two implementations of "the Porter stemmer" differ in their
edge cases, which would reintroduce exactly the divergence this section
exists to prevent. The cost is real — "running" does not match "run" — and it
is stated in the ADR rather than hidden. Filed to the backlog with this
reasoning so that whoever adds it knows what they must also add: a single
domain-owned implementation, never a per-adapter one.

### BM25 is the Lucene variant, with its constants as module constants

```python
BM25_K1 = 1.2
BM25_B = 0.75
```

Not parameters, for the reason `RRF_K` is not one: exposing them invites
tuning against a benchmark this repository does not have, and a value tuned on
one caller's corpus is the same arbitrary number with a misleading provenance.

IDF is `ln(1 + (N - df + 0.5) / (df + 0.5))`, which is positive for every
`0 <= df <= N` — the unsmoothed Robertson/Sparck-Jones form goes negative for
a term in more than half the corpus, and the usual patch is a `max(0, ...)`
floor that silently discards the signal. Choosing the form that cannot go
negative is better than clamping one that can.

Degenerate corpora are defined rather than left to divide-by-zero: an empty
corpus (`n_docs == 0`) scores `0.0`, and an `avg_doc_length` of `0` means
every document is empty, so length normalisation is the identity.

### The port gains a candidate method, not a ranked one

```python
async def lexical_candidates(
    self,
    terms: Sequence[str],
    tenant_id: TenantId,
    limit: int,
) -> LexicalCandidates: ...
```

It takes **terms, not a query string**. A string argument would put
tokenization on the far side of the port and hand it back to the adapters.

`LexicalCandidates` is a domain type carrying what scoring needs and nothing
else:

- `stats: CorpusStats` — `n_docs`, `avg_doc_length`, and `doc_frequencies`
  covering exactly the requested terms.
- `candidates: list[LexicalCandidate]` — each holding the full `StoredChunk`,
  its `doc_length` (total tokens, repeats included), and `term_frequencies`
  restricted to the requested terms.

The candidate carries the whole chunk rather than an id, because the
alternative is a second round trip per query, and every field of the chunk is
wanted by the caller that is going to rank it.

Empty `terms` returns empty candidates and zeroed statistics without touching
the store. A term absent from the corpus appears in `doc_frequencies` with
`0` rather than being omitted — an absent key and a zero frequency are
different facts, and a scorer that has to guess which it is received is a
scorer with a bug waiting.

### Candidate truncation is a stated rule, not an adapter's discretion

`limit` bounds the returned candidates, and *which* candidates survive the
bound is a ranking decision. Left to the adapter it is the divergence this
design removed, reintroduced at the last step.

The rule both adapters implement: order candidates by **the number of distinct
query terms matched, descending, then by `chunk_id` ascending**; take the
first `limit`. The tie-break is not optional — without it, two adapters
disagree about which of two equally-matching chunks survives a cut, and that
is a divergence in results.

The cost is bounded recall and it gets stated in the caller's documentation,
not only in the ADR: a chunk matching one rare, highly-informative term can be
cut before a chunk matching two common ones, so a chunk that *would* have
ranked first can be absent entirely. This is the same shape as ADR 0022's
blocking-bounded lexical recall, and it is stated for the same reason — a
missing result reads as a bug rather than as a declared limit.

`limit` is validated: negative raises `ValueError`, zero is legal and returns
no candidates with statistics still populated.

### `rank_chunks` is the whole capability, and it is pure

```python
def rank_chunks(
    terms: Sequence[str],
    candidates: LexicalCandidates,
    k: int,
) -> list[RankedChunk]: ...
```

Ordered by score descending, ties broken by `chunk_id` ascending, truncated to
`k`. `RankedChunk` carries the chunk and its BM25 score. The score is
**unbounded above and meaningful only within one result set** — BM25 is not on
`0..1`, and the type says so where it is defined, exactly as `ScoredEntity`
does for RRF.

This is not the public retrieval surface. `RankedChunk` is B2a's internal
result; B2b decides what a caller sees, and will carry this score as one
component of a fused `ScoredChunk`.

### `get_by_entity` is a plain read, not a ranked one

```python
async def get_by_entity(self, entity_id: EntityId, tenant_id: TenantId) -> list[StoredChunk]: ...
```

"Which passages mention this entity" is graph navigation, not relevance, and
folding it into the search signature as a filter would make one method answer
two questions with one `k`. Ordered by `source_id`, then `chunk_index`, then
`id` — a total order, so two adapters cannot disagree.

## Storage

**Postgres.** A `chunk_terms` table: `(tenant_id, chunk_id, term, tf)`,
primary key on all three of the first three, index on `(tenant_id, term)`.
`doc_length` becomes a column on the chunk row. Both are written inside the
existing `replace_source` and `upsert_many` statements — the term index and
the chunk it describes must never be separately visible, or statistics are
computed over a corpus that never existed, which is the argument that made
`replace_source` one operation in the first place.

`doc_frequencies` is one indexed `count(*)` per query term; a real query has a
handful of terms after stopword removal. `n_docs` and `avg_doc_length` are
aggregates over the tenant's chunks. If those aggregates become the cost
centre, the fix is maintained counters, and that is a backlog entry rather
than speculative machinery now.

**In-memory.** The same structures as dictionaries. It is the reference
implementation of the stated rules, and the compliance suite is what makes
"the same" true rather than intended.

`doc_length` and the term index are **derived**, so neither goes on
`StoredChunk`. Recomputing them at write time keeps one source of truth for
what a chunk's terms are, and that source is `tokenize`.

## Testing

The compliance suite gains cases for the three new methods, and the gate in
`tests/unit/chunks/test_compliance_coverage.py` extends to them —
`lexical_candidates` and `get_by_entity` are read methods, so both acquire a
required mutation-isolation test and a required tenant-isolation test by the
existing introspection, with no edit to the gate's logic.

Beyond the contract, the properties this design is *for*:

- **Two adapters rank identically.** Same corpus, same query, same order and
  same scores. This is the assertion adapter-side ranking would have made
  impossible, and it is the reason for the design.
- **A rare term outweighs a common one.** A term appearing in one chunk
  contributes more than a term appearing in every chunk. This is what
  distinguishes BM25 from counting.
- **Length normalisation bites.** Two chunks with the same term frequency rank
  differently when their lengths differ.
- **Truncation is deterministic.** A `limit` that cuts the candidate set
  returns the same chunks on both adapters, including which of two
  equal-matching chunks survives.

Inputs that must not be chosen carelessly, per this repository's catalogue:

- **A corpus where every chunk matches the same number of terms cannot
  distinguish the truncation ordering from the tie-break.** State a corpus
  where the match counts differ *and* a pair that ties on the count.
- **A single-term query cannot distinguish `sum over terms` from `first
  term`.** Every ranking case uses at least two query terms.
- **A corpus of one chunk cannot distinguish IDF from a constant** — `df`
  equals `n_docs` for every term. Ranking cases need a corpus where document
  frequencies differ across terms.
- **Chunks of equal length cannot distinguish `b = 0.75` from `b = 0`.** At
  least one case has documents of genuinely different lengths.
- **`limit` and `k` must differ in every test that uses both**, or a candidate
  cap and a result cap are indistinguishable.

## Layering

Everything new is in existing layers. `domain/tokenize.py`,
`domain/bm25.py`, `domain/chunk_ranking.py` in `domain`; the new types in
`domain`; the port method in `ports`; the implementations in `chunks/adapters`.
No new package, so the `exhaustive` import contract is unaffected, and no
module moves layer.

## ADR

ADR 0024, amending 0022 a second time. The clause that dies is *"the name BM25
appears nowhere under `src/`"* — and it should die loudly rather than by
drift, because this is the first time the name is honest here: a real
term-weighted ranker over a real corpus of documents. 0022's decision about
the *entity* lexical channel is untouched and restated: that channel is a
field-weighted string similarity, it still must not be called BM25, and this
ranker does not replace the thing that catches `Acme Corp`.
