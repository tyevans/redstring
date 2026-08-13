# Chunk semantic channel and fused `retrieve_chunks` — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the chunk corpus a stored-embedding retrieval channel and a
public `retrieve_chunks` entry point that fuses it with the existing BM25
channel by reciprocal rank fusion.

**Architecture:** The vector lives on the chunk row, not in `VectorStore`,
because content addressing over `(source_id, text)` is what makes a stored
vector trustworthy and that is a fact about the chunk. `ChunkStore` gains a
fifth capability protocol, `SemanticCandidateSource`, whose adapter computes
cosine similarity and whose *order* is stated by the port so the compliance
suite can assert two adapters agree. A second composition class,
`ChunkRetriever`, fuses the two channels with the same `reciprocal_rank_fusion`
the entity retriever uses, made generic for the purpose.

**Tech Stack:** Python 3.13, pydantic v2, `asyncpg` + pgvector, `pytest` +
`hypothesis`, `uv`, `import-linter`, `mypy --strict`.

**Spec:** `docs/superpowers/specs/2026-08-13-chunk-semantic-channel-design.md`
— read it before Task 1. Every decision below argues from it, and the spec
carries the rejected alternatives this plan does not repeat.

## Global Constraints

- **Never edit `pyproject.toml` dependency tables by hand.** Use `uv add` /
  `uv add --optional <extra>` / `uv remove`, then re-sync with
  `uv sync --all-extras`. No new dependency is expected by this plan.
- **Do not run ruff, bandit, `lint-imports`, or pytest as separate steps
  before committing.** They are wired into `pre-commit` and run on
  `git commit`. Write the change, then commit; re-`git add` and commit again
  when the hook fixes something in place. Running a *single named test* to
  watch it fail or pass is not the same thing and is required by the TDD
  steps below.
- **Anything you notice and do not fix goes in `BACKLOG.md` in the same
  commit that passes it by.** Not a TODO comment, not the PR body.
- **Commit messages:** imperative, sentence case, no trailing period, no
  `feat:`/`fix:` prefix. Counts and file tables belong in the body, never in
  an ADR.
- **ADR numbers are allocated at merge, not at drafting.** Draft under the
  provisional names given below; before the PR merges, run
  `git ls-tree --name-only origin/main docs/adr/ | sort | tail -1` and
  renumber the filename, the H1, *and* every inbound citation together.
- **The similarity expression is shared with the vector store verbatim:**
  `1 - (embedding <=> $2::vector) / 2`. Do not rewrite it.
- **Total orders are stated, not inferred.** Every ordering this plan adds is
  "score descending, ties by `id` ascending" and is asserted with values that
  actually collide. Ids are pinned literals, never `uuid4()` — this project
  has filed that failure three times.
- **`None` on a component score means the channel did not rank this chunk**,
  never that it scored zero.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `src/redstring/domain/fusion.py` | RRF, made generic over id type | 1 |
| `src/redstring/composition/build_graph.py` | dimension mismatch raises `DimensionMismatchError` | 2 |
| `tests/unit/composition/test_dimension_mismatch_is_one_type.py` | new gate over *every* composition point | 2 |
| `docs/adr/XXXX-one-exception-for-a-dimension-mismatch.md` | records B82's closure and the breaking change | 2 |
| `src/redstring/domain/chunk.py` | `StoredChunk.embedding` | 3 |
| `src/redstring/domain/chunk_retrieval.py` | `SemanticCandidate`, `ScoredChunk`, `ChunkRetrievalResult` | 3 |
| `src/redstring/ports/chunk_store.py` | `SemanticCandidateSource`, composed into `ChunkStore` | 4 |
| `docs/adr/XXXX-the-chunks-vector-lives-on-the-chunk.md` | records the port decision and the 0024 exception | 4 |
| `src/redstring/testing/chunk_store.py` | the shared semantic-candidates test body | 5 |
| `src/redstring/chunks/adapters/memory.py` | in-memory `dimension` + `semantic_candidates` | 6 |
| `src/redstring/chunks/adapters/postgres.py` | column, migration, SQL, `backfill_lexical_index` | 7 |
| `src/redstring/composition/retrieval.py` | `ChunkRetriever.retrieve_chunks` | 8 |
| `src/redstring/composition/index_documents.py` | optional `embeddings` provider, `embedded` counter | 9 |
| `src/redstring/__init__.py`, `docs/how-to/`, `BACKLOG.md` | exports, how-to, backlog closures | 10 |

---

### Task 1: `reciprocal_rank_fusion` becomes generic

`src/redstring/domain/fusion.py` types the function on `EntityId` and breaks
ties on "canonical lowercase `EntityId` string". Chunks need the same
function. Nothing else changes: `RRF_K = 60` stays a module constant and stays
unparameterised, per ADR 0022.

**Files:**
- Modify: `src/redstring/domain/fusion.py`
- Test: `tests/unit/domain/test_fusion.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `reciprocal_rank_fusion(rankings: Sequence[Sequence[IdT]]) -> list[tuple[IdT, float]]`,
  where `IdT` is a `TypeVar` bound to `str`. Both `EntityId` and `ChunkId` are
  `str` newtypes, so the bound holds without changing either.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/domain/test_fusion.py`. Ids are pinned literals — a
tie-break test whose ids are random cannot assert which one wins.

```python
def test_fuses_string_ids_that_are_not_entity_ids() -> None:
    """The function is generic: a ChunkId is a str newtype, like an EntityId."""
    fused = reciprocal_rank_fusion([["bbb", "aaa"], ["aaa", "bbb"]])
    assert [chunk_id for chunk_id, _ in fused] == ["aaa", "bbb"]


def test_ties_break_on_the_id_ascending() -> None:
    """Two ids at identical rank in both channels order by id, not by chance.

    `zzz` is listed first in both rankings, so position cannot decide this;
    only the stated tie-break can. Without it the order is whatever the dict
    iteration happened to produce, which passes on some runs.
    """
    fused = reciprocal_rank_fusion([["zzz", "aaa"], ["aaa", "zzz"]])
    scores = dict(fused)
    assert scores["aaa"] == scores["zzz"]
    assert [chunk_id for chunk_id, _ in fused] == ["aaa", "zzz"]


def test_a_chunk_outside_k_in_both_channels_can_beat_a_first_place() -> None:
    """The property `overfetch` exists for, stated as an example.

    `both` is second in each channel; `top_a` and `top_b` are first in one
    and absent from the other. Two seconds beat one first under RRF.
    """
    fused = reciprocal_rank_fusion([["top_a", "both"], ["top_b", "both"]])
    assert fused[0][0] == "both"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/domain/test_fusion.py -k "string_ids or ties_break or outside_k" -v`

Expected: the first fails on `mypy`/typing only at commit time, so the
observable failure is the tie-break and the generic call returning
`EntityId`-typed results. If **all three pass immediately**, stop and report:
that means the tie-break was already total and the change is a typing-only
edit, which is a finding about the plan rather than a reason to skip the task.

- [ ] **Step 3: Make the function generic**

```python
IdT = TypeVar("IdT", bound=str)


def reciprocal_rank_fusion(rankings: Sequence[Sequence[IdT]]) -> list[tuple[IdT, float]]:
```

Replace the tie-break's `EntityId`-specific canonicalisation with the id's own
lowercased string. Update the module and function docstrings: say that the
function is generic over a `str`-bound id, that `ChunkId` is a hex digest and
therefore already canonical, and keep the existing explanation of why fusion
is by rank rather than by score.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/domain/test_fusion.py -v`

Expected: PASS, including every pre-existing entity case unmodified. If an
existing assertion needs editing, this is not a refactor — stop and report.

- [ ] **Step 5: Commit**

```bash
git add src/redstring/domain/fusion.py tests/unit/domain/test_fusion.py
git commit
```

Message: `Make reciprocal rank fusion generic over its id type`. Body: say
that `ChunkId` and `EntityId` are both `str` newtypes so the bound costs
nothing, and that the tie-break had to become total before a second caller
could rely on it.

---

### Task 2: One exception type for a dimension mismatch — closes B82

**Write the gate first and watch it fail.** B82's closing paragraph is the
part that matters: nothing asserts the composition points *agree*, so a fourth
would diverge unnoticed. The gate enumerates them.

**Files:**
- Create: `tests/unit/composition/test_dimension_mismatch_is_one_type.py`
- Create: `docs/adr/XXXX-one-exception-for-a-dimension-mismatch.md`
- Modify: `src/redstring/composition/build_graph.py:479-486`
- Modify: `tests/unit/test_build_graph_embeddings.py` (the assertion on `ValueError`)
- Modify: `BACKLOG.md` (delete B82)

**Interfaces:**
- Consumes: nothing.
- Produces: every composition entry point taking an `EmbeddingProvider` and a
  store raises `DimensionMismatchError` (from `redstring.domain.exceptions`)
  on a width disagreement. `build_graph` keeps `ValueError` for a provider
  supplied without a store, or the reverse.

- [ ] **Step 1: Write the failing gate**

```python
"""Every composition point refuses a mismatched pair with the same type.

B82: `Retriever.__init__` raised `DimensionMismatchError` and `build_graph`
raised `ValueError` for the same condition, and neither `except` catches the
other. Two entry points is a divergence; three is a pattern, and the chunk
retriever is the third. The list is built here rather than asserted per
entry point because a per-entry-point test is what let the first two diverge.
"""

CASES = {
    "Retriever": ...,  # a callable raising on a mismatched pair
    "ChunkRetriever": ...,  # added by Task 8
    "build_graph": ...,
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_dimension_mismatch_is_always_a_DimensionMismatchError(name: str) -> None:
    with pytest.raises(DimensionMismatchError):
        CASES[name]()


def test_the_case_list_covers_every_composition_point() -> None:
    """Guard the guard: a gate over an empty or stale set passes vacuously.

    Every public callable in `redstring.composition` whose signature names an
    `EmbeddingProvider` must appear in CASES, so a fourth entry point fails
    here rather than diverging silently.
    """
    assert _entry_points_taking_an_embedding_provider() <= set(CASES)
```

Implement `_entry_points_taking_an_embedding_provider()` by walking
`redstring.composition`'s public callables and classes with
`typing.get_type_hints`, keeping any whose parameters mention
`EmbeddingProvider` (including under `| None`). `ChunkRetriever` does not
exist yet — leave its `CASES` entry out and add it in Task 8; the
guard-the-guard test is what will force you to.

- [ ] **Step 2: Run the gate to verify it fails**

Run: `uv run pytest tests/unit/composition/test_dimension_mismatch_is_one_type.py -v`

Expected: the `build_graph` case FAILS with `ValueError` not being a
`DimensionMismatchError`. The `Retriever` case passes. **Both outcomes are
required** — if `build_graph` passes, the wrong callable is being invoked.

- [ ] **Step 3: Change `build_graph`'s dimension check only**

At `build_graph.py:479-486`, keep the message verbatim and change the type:

```python
if provider is not None and store is not None and provider.dimension != store.dimension:
    raise DimensionMismatchError(expected=store.dimension, actual=provider.dimension)
```

The message the old `ValueError` carried — two models' vectors are not
comparable even at equal dimension, so point the run at a store built for this
model — moves into the docstring, which is where it stays true. **Do not
touch** the half-configured check (a provider without a store): that stays a
`ValueError`, because arity and disagreement are different mistakes.

- [ ] **Step 4: Update the existing assertion and run both**

`tests/unit/test_build_graph_embeddings.py` asserts `ValueError`. Change only
the mismatch case; leave the half-configured case asserting `ValueError` and
add a comment saying why the two differ.

Run: `uv run pytest tests/unit/composition/test_dimension_mismatch_is_one_type.py tests/unit/test_build_graph_embeddings.py -v`

Expected: PASS.

- [ ] **Step 5: Write the ADR and delete B82**

`docs/adr/XXXX-one-exception-for-a-dimension-mismatch.md`, provisional number.
Status: Accepted. Record that `0012` and `0017` stand. Decision: the type that
names the condition wins; the half-configured case keeps `ValueError`;
the gate is introspective so a fourth entry point is covered by construction.
Consequences: **breaking** for callers catching `ValueError` around
`build_graph`, and it takes a version bump. No counts, no file tables.

Delete B82 from `BACKLOG.md` in this commit.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit
```

Message: `Refuse every dimension mismatch with one exception type`. Body: the
breaking change, why the half-configured case is excluded, and that the gate
was written red first because B82 said it must be.

---

### Task 3: The domain types

**Files:**
- Modify: `src/redstring/domain/chunk.py`
- Create: `src/redstring/domain/chunk_retrieval.py`
- Test: `tests/unit/domain/test_chunk.py`, `tests/unit/domain/test_chunk_retrieval.py`

**Interfaces:**
- Consumes: `StoredChunk` from Task 3's own module.
- Produces:
  - `StoredChunk.embedding: list[float] | None = None`
  - `SemanticCandidate(chunk: StoredChunk, score: float)`
  - `ScoredChunk(chunk: StoredChunk, score: float, semantic: float | None = None, lexical: float | None = None)`
  - `ChunkRetrievalResult(query: str, matches: list[ScoredChunk])`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_stored_chunk_has_no_embedding_by_default() -> None:
    """None means not embedded, and is distinct from a zero vector.

    Built directly rather than through a factory: the defaults on the public
    type are what a caller constructing one gets, and a helper that passes
    every field never executes them.
    """
    chunk = StoredChunk(
        id="a" * 64,
        tenant_id=TenantId(UUID(int=1)),
        source_id=SourceId("doc-1"),
        text="Ada Lovelace wrote the first algorithm.",
        chunk_index=0,
        start_char=0,
        end_char=39,
    )
    assert chunk.embedding is None


def test_a_scored_chunk_distinguishes_unranked_from_zero() -> None:
    """`None` means the channel did not rank it; 0.0 means it ranked it last."""
    unranked = ScoredChunk(chunk=_chunk(), score=0.5, semantic=0.9)
    assert unranked.lexical is None
    scored_zero = ScoredChunk(chunk=_chunk(), score=0.5, semantic=0.9, lexical=0.0)
    assert scored_zero.lexical == 0.0
    assert scored_zero != unranked
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/domain/test_chunk.py tests/unit/domain/test_chunk_retrieval.py -v`
Expected: FAIL — `embedding` is not a field; `chunk_retrieval` does not import.

- [ ] **Step 3: Add the field and the module**

`StoredChunk.embedding: list[float] | None = None`, with a docstring saying
`None` means not embedded, that a re-chunk produces a new id rather than
invalidating this vector, and that this is why the vector is safe here.

`domain/chunk_retrieval.py` holds the three models above, each a pydantic
`BaseModel`, mirroring `domain/retrieval.py`'s `ScoredEntity` /
`RetrievalResult` shape. `ScoredChunk`'s docstring states the `None` rule and
cites ADR 0022 for why the components are retained at all.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/domain/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Message: `Give a stored chunk a vector and a scored chunk two components`.

---

### Task 4: The `SemanticCandidateSource` capability

**Files:**
- Modify: `src/redstring/ports/chunk_store.py`
- Modify: `tests/unit/chunks/test_capability_segregation.py`
- Modify: `tests/unit/chunks/test_compliance_coverage.py`
- Create: `docs/adr/XXXX-the-chunks-vector-lives-on-the-chunk.md`

**Interfaces:**
- Consumes: `SemanticCandidate` (Task 3).
- Produces:

```python
@runtime_checkable
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


@runtime_checkable
class ChunkStore(
    ChunkWriter, ChunkReader, LexicalCandidateSource, SemanticCandidateSource, ChunkPurge, Protocol
): ...
```

- [ ] **Step 1: Update the two gates so they fail**

`tests/unit/chunks/test_capability_segregation.py` asserts the four-way split
— make it five.

`tests/unit/chunks/test_compliance_coverage.py` introspects the port for
chunk-returning methods and **will silently skip a method whose return type it
cannot resolve**. Two edits, both required:
- add `SemanticCandidate` to `_PORT_NAMESPACE` (the port annotates under
  `if TYPE_CHECKING`, so the name is not otherwise resolvable), and
- add `SemanticCandidate` to the `_mentions` target set beside `StoredChunk`
  and `LexicalCandidates`.

Its guard-the-guard test pins the method set as a literal:
```python
assert read_methods() == {"get", "get_by_source", "get_by_entity", "lexical_candidates"}
```
Add `"semantic_candidates"`. That literal failing is the gate working, not an
obstacle.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/chunks/test_capability_segregation.py tests/unit/chunks/test_compliance_coverage.py -v`
Expected: FAIL — the protocol does not exist; the method set does not match.

- [ ] **Step 3: Add the protocol and the docstrings**

The method docstring states, as contract rather than as note:
- **The order is total**: score descending, then `id` ascending. Two adapters
  cutting different chunks from an equal pair is a divergence in results.
- Chunks with `embedding is None` are **not candidates** — skipped, not
  scored zero.
- `min_score` filters **before** `limit`, matching `VectorStore.search`.
- `limit = 0` returns nothing; a negative `limit` raises `ValueError`.
- A vector whose width is not `dimension` raises `DimensionMismatchError`.
- The returned chunks are the caller's; mutating them cannot change stored
  state.

Update the module docstring: the sentence *"Semantic search over this corpus
-- chunk embeddings, a fused public result type -- is still a separate piece
of work and still has no method here"* is now false. Replace it with what the
two channels are and why the lexical one scores in the domain while this one
scores in the adapter.

Add the content-addressing prose to `ChunkWriter.upsert_many` (B97's cheap
half): *a chunk id is content-addressed over `(source_id, text)`; re-using an
id for different text is outside the contract.*

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/chunks/ -v`
Expected: the two gates PASS. The compliance-coverage gate will now demand
`test_semantic_candidates_returns_copies` and
`test_semantic_candidates_never_crosses_tenants` on the compliance class —
that failure is **expected here and closed by Task 5**. Note it in the commit
body; do not add the methods yet.

- [ ] **Step 5: Write the ADR**

`docs/adr/XXXX-the-chunks-vector-lives-on-the-chunk.md`, provisional number.
Status: Accepted; amends `0023` (the deferred semantic search is built) and
`0026` (a fifth capability). `0002`, `0006`, `0012`, `0017`, `0022`, `0024`
stand — say so explicitly. Decisions: not `VectorStore` and why; the adapter
scores and why that is a stated exception to `0024` rather than a reversal;
the store declares its width at construction; composition embeds because
extraction writes to no store and `chunks` may not import `llm`.

- [ ] **Step 6: Commit and record the deferral**

Update B97 in `BACKLOG.md` — the prose landed, the executable half is still
open, and say why (it needs a decision about whether the port promises
last-write-wins on derived state). Do not delete it.

Message: `Add a semantic candidate capability to the chunk store port`.

---

### Task 5: The shared compliance body

The semantics of a port method with two adapters live here and nowhere else.
This task writes tests against a port no adapter implements yet; they fail,
and Tasks 6 and 7 turn them green.

**Files:**
- Modify: `src/redstring/testing/chunk_store.py`

**Interfaces:**
- Consumes: `SemanticCandidateSource` (Task 4).
- Produces: `ChunkStoreCompliance.new_store()` must now return a store built
  at a **known dimension**. Add a class attribute `DIMENSION = 4` and require
  subclasses to build at that width; document it in the class docstring beside
  the existing `new_store` contract.

- [ ] **Step 1: Write the test body**

Add a banner comment marking the semantic block, matching the lexical block's
style at L849. The cases, each with a docstring saying what wrong
implementation it excludes:

```python
async def test_semantic_candidates_orders_by_score_descending(self, store): ...
async def test_semantic_candidates_breaks_ties_on_id_ascending(self, store):
    """Two chunks at an identical similarity order by id, not by insertion.

    The vectors are chosen so the similarities are *equal*, and the ids are
    the digests of two texts whose order is known — a tie-break test whose
    scores merely differ tests nothing about the tie-break.
    """


async def test_semantic_candidates_skips_unembedded_chunks(self, store):
    """A chunk with no vector is absent, not present with score 0."""


async def test_semantic_candidates_applies_min_score_before_limit(self, store): ...
async def test_semantic_candidates_with_a_zero_limit_returns_nothing(self, store):
    """Pinned as an example, not left to a sampler. See BACKLOG B125."""


async def test_semantic_candidates_rejects_a_negative_limit(self, store): ...
async def test_semantic_candidates_rejects_a_vector_of_the_wrong_width(self, store): ...
async def test_semantic_candidates_returns_copies(self, store): ...
async def test_semantic_candidates_never_crosses_tenants(self, store): ...
```

The last two are named by convention because
`tests/unit/chunks/test_compliance_coverage.py` looks for exactly those
strings. `returns_copies` must use the existing `_mutate` helper *and* mutate
the returned `embedding` list, then assert a fresh read is unaffected — a
shallow copy that shares the vector passes every behavioural assertion.

Extend the static `_chunk` helper with an optional `embedding` argument,
defaulting to `None`, so existing call sites are untouched.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/chunks/test_memory_store.py -k semantic -v`
Expected: FAIL — `InMemoryChunkStore` has no `semantic_candidates`.

- [ ] **Step 3: Commit the red suite**

A shared suite landing before its adapters is deliberate and is the order this
project's rules ask for. Say so in the commit body.

Message: `State the semantic candidate contract in the chunk compliance suite`.

---

### Task 6: `InMemoryChunkStore`

**Files:**
- Modify: `src/redstring/chunks/adapters/memory.py`
- Modify: `tests/unit/chunks/test_memory_store.py` (the `new_store` hook)

**Interfaces:**
- Consumes: the compliance body (Task 5).
- Produces: `InMemoryChunkStore(*, dimension: int)`; `dimension` property;
  `semantic_candidates` as specified.

- [ ] **Step 1: Run the compliance suite to see it red**

Run: `uv run pytest tests/unit/chunks/test_memory_store.py -k semantic -v`
Expected: FAIL.

- [ ] **Step 2: Implement**

`__init__` takes a required keyword-only `dimension: int`; a non-positive
dimension raises `ValueError`. `new_store` in the test file builds at
`self.DIMENSION`.

`semantic_candidates`: reject a negative `limit` with `ValueError` and a
wrong-width vector with `DimensionMismatchError` **before** touching state;
skip chunks whose `embedding is None`; compute cosine similarity mapped to
0..1 by the same `1 - distance / 2` convention the SQL uses; apply `min_score`
before truncation; sort by `(-score, id)`; return deep copies.

- [ ] **Step 3: Run to verify green**

Run: `uv run pytest tests/unit/chunks/test_memory_store.py -v`
Expected: PASS, including the two isolation cases.

- [ ] **Step 4: Commit**

Message: `Implement semantic candidates in the in-memory chunk store`.

---

### Task 7: `PostgresChunkStore`, the column and the owed migration

The largest task, and the only one with a schema change. It carries the
migration B89 says is **already owed** for `doc_length`.

**Files:**
- Modify: `src/redstring/chunks/adapters/postgres.py`
- Modify: `tests/unit/chunks/test_postgres_schema.py`
- Modify: `tests/integration/chunks/test_postgres_store.py`

**Interfaces:**
- Consumes: the compliance body (Task 5).
- Produces: `PostgresChunkStore(pool, *, table=..., dimension: int)`;
  `dimension` property; `semantic_candidates`;
  `backfill_lexical_index() -> int`.

- [ ] **Step 1: Write the server-free SQL-shape tests, red**

In `tests/unit/chunks/test_postgres_schema.py`:

```python
def test_the_schema_alters_an_existing_table_onto_the_current_columns() -> None:
    """`CREATE TABLE IF NOT EXISTS` adds nothing to a table that predates a column.

    B89: a `kg_chunks` created before the lexical work never got `doc_length`,
    and every query naming `_COLUMNS` fails against it. The ALTERs are the
    repair, and they ship with the column that made a second one necessary.
    """
    statements = " ".join(store._schema_statements())
    assert "ADD COLUMN IF NOT EXISTS doc_length" in statements
    assert "ADD COLUMN IF NOT EXISTS embedding" in statements


def test_the_similarity_expression_matches_the_vector_stores() -> None:
    """One definition of cosine similarity in this library, not two."""
    from redstring.vector.adapters.pgvector import _SCORE as VECTOR_SCORE

    assert _SCORE == VECTOR_SCORE
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/chunks/test_postgres_schema.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the schema and the writes**

Append to `_schema_statements()` — after the `CREATE TABLE`, so a fresh
database is created and then no-op altered, which keeps one code path:

```python
(f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS doc_length integer NOT NULL DEFAULT 0",)
(f"ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS embedding vector({self._dimension})",)
```

`ensure_schema` must also run `CREATE EXTENSION IF NOT EXISTS vector`, as
`PgVectorStore` does. `embedding` is nullable — that is the `None` state.

Thread the column through `_COLUMNS`, `_INCOMING` (the `jsonb_to_recordset`
record shape), `_ON_CONFLICT`, `encode()` and `_chunk_from()`. **`_ON_CONFLICT`
omits `embedding`**, exactly as it omits `doc_length`, for the same
content-addressing reason — and that omission is B97's open half, already
noted in Task 4.

`semantic_candidates` follows `_candidates_sql`'s shape: limit in a CTE before
joining the wide table, `WHERE embedding IS NOT NULL`, `min_score` in the
`WHERE` so it applies before `LIMIT`, and
`ORDER BY score DESC, id ASC`. Guard the vector's width with
`DimensionMismatchError` before building SQL. Keep the `# nosec B608` markers
resting on the `_IDENTIFIER` regex.

`backfill_lexical_index() -> int` recomputes `doc_length` and the term rows
from the stored `text` using `domain.tokenize` — the same function the write
path uses, which is what makes a backfilled row identical to a fresh one. It
is idempotent and returns rows touched.

- [ ] **Step 4: Extend the integration suite**

`tests/integration/chunks/test_postgres_store.py` runs the compliance body;
give its `new_store` the dimension. Two tests must be added, and the first is
the one that matters:

```python
async def test_ensure_schema_repairs_a_table_created_without_the_new_columns(pool):
    """The ALTER is proved against a table that actually lacks the columns.

    An `ADD COLUMN IF NOT EXISTS` run only against a table that already has
    the column is a statement never observed to do anything. This creates the
    pre-migration table by hand, runs `ensure_schema`, and asserts the columns
    arrive and a query naming `_COLUMNS` then succeeds.
    """


async def test_backfill_lexical_index_makes_a_pre_migration_row_rankable(pool):
    """A backfill asserted only by its return count is a counter, not a repair.

    Rows written before the term index rank as empty documents. Assert the
    ranking is wrong before and right after.
    """
```

`test_ensure_schema_creates_the_table_from_nothing` compares a fresh table's
columns against the worker table and **will fail until the worker table is
dropped** — that is by design and its failure message says so. Drop it.

- [ ] **Step 5: Run**

Run: `uv run pytest tests/unit/chunks/ -v`, then, with
`docker-compose.test.yml` up:
`uv run pytest -m integration tests/integration/chunks/ -v`

Expected: PASS. Integration cannot run under xdist (B10f) — use `-p no:xdist`
if the default config parallelises.

- [ ] **Step 6: Commit**

Message: `Store a chunk embedding in Postgres, and repair the owed columns`.
Body: the two ALTERs, why they ship together, and that the worker table had to
be dropped.

---

### Task 8: `ChunkRetriever`

**Files:**
- Modify: `src/redstring/composition/retrieval.py`
- Modify: `tests/unit/composition/test_dimension_mismatch_is_one_type.py`
- Test: `tests/unit/composition/test_chunk_retrieval.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 4, 6.
- Produces:

```python
class ChunkRetriever:
    def __init__(
        self,
        *,
        embeddings: EmbeddingProvider,
        chunks: ChunkStore,
        overfetch: int = 3,
    ) -> None: ...

    async def retrieve_chunks(
        self,
        query: str,
        tenant_id: TenantId,
        *,
        k: int = 10,
        mode: RetrievalMode = RetrievalMode.HYBRID,
    ) -> ChunkRetrievalResult: ...
```

The `chunks` parameter is annotated as the intersection of the two capability
protocols it uses; if that is awkward under `mypy --strict`, annotate it
`ChunkStore` and say in the docstring that only two capabilities are called.

- [ ] **Step 1: Write the failing tests**

Cover, each with a docstring naming the wrong implementation it excludes:
a mismatched pair raising `DimensionMismatchError` at construction;
`overfetch < 1` raising `ValueError`; a blank query raising; a negative `k`
raising; `k=0` returning empty; `SEMANTIC` and `LEXICAL` modes using one
channel each; `HYBRID` fusing both; **a `HYBRID` query over an unembedded
corpus still returning lexical results**; and a chunk ranked outside `k` in
both channels beating a first place, which is the property `overfetch` exists
for.

Then add `"ChunkRetriever"` to `CASES` in the Task 2 gate — the
guard-the-guard test fails until you do.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/composition/ -v`
Expected: FAIL, including the guard-the-guard test naming `ChunkRetriever`.

- [ ] **Step 3: Implement**

Mirror `Retriever` closely: same guard order, same `per_channel = k *
overfetch`, `reciprocal_rank_fusion` over the two id lists truncated to `k`,
and `ScoredChunk` carrying both component scores with `None` for a channel
that did not rank the chunk. The lexical channel is `tokenize(query)` →
`lexical_candidates` → `rank_chunks`. The semantic channel is
`embeddings.embed([query])` → `semantic_candidates`.

The class docstring states the two limits a caller would otherwise read as
bugs: lexical recall is bounded by the candidate `limit` (ADR 0024), and a
corpus with no embeddings answers a semantic query with nothing rather than
raising, because "unembedded" is a per-row fact.

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/unit/composition/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Message: `Fuse the chunk corpus's two channels behind retrieve_chunks`.

---

### Task 9: `index_documents` embeds when asked

**Files:**
- Modify: `src/redstring/composition/index_documents.py`
- Test: `tests/unit/composition/test_index_documents.py`

**Interfaces:**
- Consumes: Tasks 3, 6.
- Produces: `index_documents(..., embeddings: EmbeddingProvider | None = None)`;
  `IndexReport.embedded: int`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_without_a_provider_no_chunk_is_embedded_and_no_model_is_called() -> None:
    """The docstring's no-per-token-cost promise is kept by the default.

    The provider is a spy that raises if called.
    """


async def test_with_a_provider_every_chunk_carries_its_vector() -> None: ...


async def test_the_report_counts_chunks_embedded() -> None:
    """A counter is asserted non-zero under the condition it counts.

    `recurring-defects.md` §3: a counter never incremented looks exactly like
    a condition never met. Assert it differs from `written` too, so the two
    cannot be wired to the same expression.
    """
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/composition/test_index_documents.py -v`
Expected: FAIL — unexpected keyword argument.

- [ ] **Step 3: Implement**

Add the keyword-only parameter, defaulting to `None`. When supplied, embed
each document's chunk texts in **one** `embed` call per document — the port is
order-preserving and batching is what makes it affordable — and attach each
vector to its `StoredChunk` before `record_chunking`. Increment `embedded`.

Amend the function docstring: the no-model-calls claim becomes conditional on
the argument, stated rather than quietly falsified.

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/unit/composition/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Message: `Embed chunks during indexing when a provider is supplied`.

---

### Task 10: The public surface, the docs, and the backlog

**Files:**
- Modify: `src/redstring/__init__.py`
- Create: `docs/how-to/retrieve-chunks.md`
- Modify: `docs/reference/`, `mkdocs.yml` nav
- Modify: `BACKLOG.md`

- [ ] **Step 1: Add the exports and watch the gates name the closure**

Export `ChunkRetriever`, `retrieve_chunks`'s result types
(`ChunkRetrievalResult`, `ScoredChunk`), `SemanticCandidate`, and
`SemanticCandidateSource`. ADR 0006's signature gate walks the MRO and will
name anything a signature mentions that is not exported — let it, and export
the closure rather than trimming the signature.

Update the hand-written module docstring narrative (the Retrieval and Ports
bullets), which no gate checks.

- [ ] **Step 2: Run the three public-surface gates**

Run: `uv run pytest tests/unit/test_public_api.py -v` (or whichever modules
hold the three gates named in `CLAUDE.md`)
Expected: PASS.

- [ ] **Step 3: Write the how-to**

`docs/how-to/retrieve-chunks.md`, in the shape of
`docs/how-to/retrieve-entities.md`. It must state the two limits in the
caller's own documentation, where a missing result would otherwise read as a
bug: bounded lexical recall, and a semantic query over an unembedded corpus
returning nothing. Add it to `mkdocs.yml`'s nav — `mkdocs --strict` is a CI
job and a page outside the nav or a broken ADR link fails it.

- [ ] **Step 4: Update the backlog**

- Delete B89 (this is B2b).
- B10k gains the chunk semantic scan as a second instance of the exact-scan
  cost.
- B81 gains the chunk channels: nothing here measures whether fusion helps.
- Add an entry for the wider `StoredChunk` crossing the port on every read.
- Confirm B97 was updated in Task 4 and B82 deleted in Task 2.

- [ ] **Step 5: Renumber the two ADRs and commit**

```bash
git ls-tree --name-only origin/main docs/adr/ | sort | tail -1
```

Renumber both drafts against that: **the filename, the H1, and every inbound
citation, in one commit.** Renaming the file alone is the documented failure
mode. Add both to `docs/adr/index.md`.

Message: `Export the chunk retrieval surface and document its limits`.

---

## Self-review

**Spec coverage.** Vector on the chunk → T3, T6, T7. Fifth capability → T4.
Adapter-side scoring and the shared expression → T7 step 1. Width at
construction → T6, T7. B82 → T2. `ScoredChunk` → T3. Generic RRF → T1.
`ChunkRetriever` → T8. Composition embeds → T9. Migration and backfill → T7.
B97 prose → T4. ADRs → T2, T4, renumbered in T10. Testing section → T1's
pinned ids, T5's tie-break and pinned `limit=0`, T2's red-first gate, T7's
pre-migration table and backfill-changes-something, T4's three gate edits.

**Type consistency.** `semantic_candidates` returns `list[SemanticCandidate]`
in T3, T4, T5, T6, T7 alike; `ScoredChunk` fields are the same four in T3 and
T8; `dimension` is a property on the port and both adapters.

**Known gap, deliberate.** No accuracy measurement of the fused chunk ranking
— B81 covers it and T10 extends it. Nothing in this repository can settle it
without a graded retrieval corpus.
