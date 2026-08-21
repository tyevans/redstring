# `StoredChunk.id` Is Derived, Not Supplied — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close BACKLOG B97 by making it impossible to construct a `StoredChunk` whose `id` is not `chunk_id(source_id, text)`, so the two `ChunkStore` adapters cannot diverge on derived state.

**Architecture:** `StoredChunk.id` stops being an input field and becomes a `pydantic.computed_field` over `(source_id, text)`; `model_config` gains `extra="forbid"` so any caller still passing `id=` fails loudly instead of being silently ignored. The divergence B97 describes then has no constructible input, which is a stronger gate than a compliance case asserting the adapters agree about it.

**Tech Stack:** Python 3.12+, pydantic v2, pytest, `uv`.

**Spec:** No separate spec file — the design was settled in chat and is restated in full under "Design" below, and the ADR written in Task 3 is the durable record.

## Design

`chunks/adapters/postgres.py` skips re-deriving three columns on an id conflict — `doc_length` and `embedding` are omitted from `_ON_CONFLICT`'s `SET` list, and term rows are written `ON CONFLICT DO NOTHING`. Every one of those is justified by the same claim: a chunk id is content-addressed over `(source_id, text)`, so a write reusing an id must be writing the same text. `InMemoryChunkStore` makes no such assumption — it tokenizes at query time — so a caller who reuses an id for different text gets ranking over the *new* text from one adapter and the *old* text from the other.

The claim the adapters rest on is true of `chunk_id` and not enforced anywhere: `StoredChunk.id` is a caller-supplied `str`.

**The contract chosen is "a chunk id is derived", not "derived state is last-write-wins".** The alternative — having Postgres re-derive `doc_length`, the term index and `embedding` on conflict — was rejected because it contradicts the design already shipped (`domain/chunk.py` records that positional identity was rejected precisely so a stale embedding can never describe changed text) and because the term-row half needs a DELETE-then-INSERT that `_TERMS_ON_CONFLICT`'s docstring already argues is unsafe in one statement.

**This is a breaking change**, deliberately: callers can no longer self-assign chunk ids. `model_dump()` still emits `id` (pydantic computed fields serialize), so the event-log wire shape is unchanged. A replayed `DocumentChunked` whose stored id was *not* content-addressed now resolves to the derived id rather than the stored one, converging the log onto the contract.

## Global Constraints

- Run project-scoped commands through `uv run`. Never edit `pyproject.toml` dependency tables by hand.
- Do **not** run ruff, bandit, or `lint-imports` as separate steps. They run on `git commit` via pre-commit.
- Do **not** run the full test suite. Run only the specific test files named in each task, with `-p no:randomly`.
- Anything noticed and not fixed lands in `BACKLOG.md` in the same commit that passes it by.
- Prefer many small commits.

---

### Task 1: Prove every existing call site already derives its id

This task adds the constraint as a validator only. Nothing should break — every caller in the tree is believed to build ids the content-addressed way already, and this task is what turns that belief into evidence before Task 2 removes the field. If it goes red somewhere, that is a finding, not a nuisance: record it before proceeding.

**Files:**
- Modify: `src/redstring/domain/chunk.py`
- Test: `tests/unit/domain/test_chunk.py`

**Interfaces:**
- Consumes: `chunk_id(source_id: SourceId, text: str) -> ChunkId` from `redstring.domain.chunk`.
- Produces: `StoredChunk` now raises `pydantic.ValidationError` when `id != chunk_id(source_id, text)`. Task 2 replaces this validator entirely.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/domain/test_chunk.py`:

```python
def test_a_stored_chunk_whose_id_is_not_derived_from_its_text_is_rejected() -> None:
    """The id is what both adapters rest on to skip re-deriving state (B97).

    The wrong id here is a *real other chunk's* id rather than a random
    string, because a random string could be rejected by any format check
    the field ever grows, and this test would then pass for a reason that
    has nothing to do with derivation.
    """
    source = SourceId("doc-1")
    with pytest.raises(ValidationError, match="content-addressed"):
        StoredChunk(
            id=chunk_id(source, "some other passage"),
            tenant_id=TenantId(uuid4()),
            source_id=source,
            text="the passage actually being stored",
            chunk_index=0,
            start_char=0,
            end_char=33,
        )
```

Check the imports at the top of the file and add whichever of `pytest`, `uuid4`, `ValidationError`, `chunk_id`, `StoredChunk`, `SourceId`, `TenantId` are missing.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/domain/test_chunk.py -v -p no:randomly`
Expected: the new test FAILS with `DID NOT RAISE`. Every other test in the file passes.

- [ ] **Step 3: Write the validator**

In `src/redstring/domain/chunk.py`, add to `StoredChunk` (after the existing `_metadata_is_storable` validator). It must be a `model_validator(mode="after")`, not a `field_validator`, because it reads three fields at once:

```python
    @model_validator(mode="after")
    def _id_is_derived(self) -> StoredChunk:
        expected = chunk_id(self.source_id, self.text)
        if self.id != expected:
            raise ValueError(
                f"id must be content-addressed over (source_id, text): "
                f"expected {expected}, got {self.id}"
            )
        return self
```

Add `model_validator` to the `pydantic` import.

- [ ] **Step 4: Run the domain tests**

Run: `uv run pytest tests/unit/domain/test_chunk.py -v -p no:randomly`
Expected: PASS.

- [ ] **Step 5: Run every test module that constructs a `StoredChunk`**

This is the evidence the task exists to produce. Run exactly these, in one invocation:

```
uv run pytest -p no:randomly -q \
  tests/unit/domain/test_chunk.py \
  tests/unit/domain/test_chunk_ranking.py \
  tests/unit/domain/test_chunk_retrieval.py \
  tests/unit/chunks/ \
  tests/unit/projections/test_chunk.py \
  tests/unit/events/test_payloads.py \
  tests/unit/aggregates/test_document.py \
  tests/unit/composition/test_chunk_retrieval.py \
  tests/unit/composition/test_themes.py \
  tests/unit/extraction/
```

Expected: PASS. If anything fails, **stop and report it** — a failure here means some call site was not content-addressed, which is B97 occurring in this repo rather than only in a hypothetical caller's. Do not "fix" it by editing the expected id until you have said what it was.

- [ ] **Step 6: Commit**

```bash
git add src/redstring/domain/chunk.py tests/unit/domain/test_chunk.py
git commit -m "Reject a StoredChunk id that is not derived from its text"
```

---

### Task 2: Remove the field, so the bad state is unconstructible

**Files:**
- Modify: `src/redstring/domain/chunk.py`
- Modify: `src/redstring/extraction/corpus.py:132-142`
- Modify: `src/redstring/chunks/adapters/postgres.py:924-936` (`_row_to_chunk`)
- Modify: `src/redstring/testing/chunk_store.py` (3 sites)
- Modify (drop the `id=` argument only): `tests/unit/domain/test_chunk.py`, `tests/unit/domain/test_chunk_ranking.py`, `tests/unit/domain/test_chunk_retrieval.py`, `tests/unit/chunks/test_memory_store.py`, `tests/unit/chunks/test_postgres_schema.py`, `tests/unit/chunks/test_capability_segregation.py`, `tests/unit/projections/test_chunk.py`, `tests/unit/projections/log_builder.py`, `tests/unit/events/test_payloads.py`, `tests/unit/aggregates/test_document.py`, `tests/unit/composition/test_chunk_retrieval.py`, `tests/unit/composition/test_themes.py`

**Interfaces:**
- Consumes: nothing from Task 1 survives — its validator is deleted here.
- Produces: `StoredChunk(...)` no longer accepts `id`; `StoredChunk.id` is a read-only `property`-like computed field returning `ChunkId`. Passing `id=` raises `pydantic.ValidationError` naming an extra field. `model_dump()` still contains `"id"`.

- [ ] **Step 1: Write the failing tests**

Replace the test added in Task 1 with these three in `tests/unit/domain/test_chunk.py`:

```python
def test_id_is_derived_rather_than_supplied() -> None:
    source = SourceId("doc-1")
    chunk = StoredChunk(
        tenant_id=TenantId(uuid4()),
        source_id=source,
        text="the passage actually being stored",
        chunk_index=0,
        start_char=0,
        end_char=33,
    )
    assert chunk.id == chunk_id(source, "the passage actually being stored")


def test_supplying_an_id_is_rejected_rather_than_ignored() -> None:
    """`extra="forbid"` is the point: silently ignoring `id=` would leave a
    caller believing they had chosen the id, which is B97 with a friendlier
    face. The value passed here is a *correct* derived id, so the rejection
    cannot be passing because the value was wrong."""
    source = SourceId("doc-1")
    with pytest.raises(ValidationError, match="id"):
        StoredChunk(
            id=chunk_id(source, "a passage"),
            tenant_id=TenantId(uuid4()),
            source_id=source,
            text="a passage",
            chunk_index=0,
            start_char=0,
            end_char=9,
        )


def test_the_serialised_shape_still_carries_the_id() -> None:
    """`DocumentChunked` puts these on the event log. A computed field that
    stopped serialising would change the log's shape without changing a
    single call site, and nothing else in this file would notice."""
    chunk = StoredChunk(
        tenant_id=TenantId(uuid4()),
        source_id=SourceId("doc-1"),
        text="a passage",
        chunk_index=0,
        start_char=0,
        end_char=9,
    )
    dumped = chunk.model_dump()
    assert dumped["id"] == chunk.id
    assert StoredChunk.model_validate({k: v for k, v in dumped.items() if k != "id"}).id == chunk.id
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/domain/test_chunk.py -v -p no:randomly`
Expected: `test_id_is_derived_rather_than_supplied` and `test_the_serialised_shape_still_carries_the_id` FAIL with a missing-`id` validation error; `test_supplying_an_id_is_rejected_rather_than_ignored` FAILS with `DID NOT RAISE`.

- [ ] **Step 3: Make `id` computed**

In `src/redstring/domain/chunk.py`:

1. Delete the `_id_is_derived` validator added in Task 1.
2. Delete the `id: ChunkId` field declaration.
3. Add `model_config = ConfigDict(extra="forbid")` as the first statement in the class body, importing `ConfigDict` from `pydantic`.
4. Add, after the field declarations and before the validators:

```python
    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> ChunkId:
        """This passage's identity, derived rather than supplied.

        A field here would let a caller name an id unrelated to the text,
        and both adapters skip re-deriving `doc_length`, the term index and
        `embedding` on an id conflict precisely because they assume no
        caller can (BACKLOG B97). A computed field makes that assumption
        true instead of merely documented.

        It is `computed_field` rather than a plain `property` because
        `DocumentChunked` carries these to the event log: a plain property
        would drop `id` from `model_dump()` and change the log's shape.
        """
        return chunk_id(self.source_id, self.text)
```

Add `computed_field` to the `pydantic` import.

- [ ] **Step 4: Run the domain tests**

Run: `uv run pytest tests/unit/domain/test_chunk.py -v -p no:randomly`
Expected: PASS.

- [ ] **Step 5: Sweep the two `src/` call sites**

In `src/redstring/extraction/corpus.py`, delete the `id=ident,` line from the `StoredChunk(...)` call at line 133. `ident` is still used as the dict key in the comprehension, so leave the rest alone.

In `src/redstring/chunks/adapters/postgres.py`'s `_row_to_chunk`, delete `id=row["id"],`. Replace it with a comment stating why the column is read past:

```python
        # `id` is not passed: it is computed from `(source_id, text)`, which
        # is the same value the column holds for any row this adapter wrote.
        # A legacy row whose stored id was not content-addressed therefore
        # comes back under its derived id -- see the ADR; that row could only
        # have been written before the id became underivable.
```

Then check whether `row["id"]` is still referenced anywhere in that function or its callers; if `_SELECT_COLUMNS` no longer needs `id`, **leave it in anyway** — it is the primary key and the `WHERE` clauses use it.

- [ ] **Step 6: Sweep `src/redstring/testing/chunk_store.py`**

Find the three `StoredChunk(` calls. In `_chunk`, the line `id=chunk_id(source, text),` goes; keep the surrounding docstring but update the sentence claiming the helper builds the id, since it no longer does. Do the same at the other two sites.

- [ ] **Step 7: Sweep the test call sites**

For each file in the "Modify (drop the `id=` argument only)" list above, find every `StoredChunk(` call and delete its `id=` argument. Most pass `id=chunk_id(...)` and the deletion is trivial. Where a test binds the id to a local first (`shared = chunk_id(...)`) and asserts on it later, keep the local — only the constructor argument goes.

Two files need more than a deletion:
- `tests/unit/chunks/test_postgres_schema.py` may assert on the `_INCOMING`/`_COLUMNS` SQL text. Do not change those constants; `id` is still a column.
- `tests/unit/domain/test_chunk.py` has seven call sites, three of which you rewrote in Step 1.

- [ ] **Step 8: Run every affected module**

```
uv run pytest -p no:randomly -q \
  tests/unit/domain/test_chunk.py \
  tests/unit/domain/test_chunk_ranking.py \
  tests/unit/domain/test_chunk_retrieval.py \
  tests/unit/chunks/ \
  tests/unit/projections/test_chunk.py \
  tests/unit/events/test_payloads.py \
  tests/unit/aggregates/test_document.py \
  tests/unit/composition/test_chunk_retrieval.py \
  tests/unit/composition/test_themes.py \
  tests/unit/extraction/
```

Expected: PASS. Do not run anything wider.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "Derive StoredChunk.id rather than accepting it from the caller"
```

The pre-commit hook may reformat. If it does, `git add -A` and commit again.

---

### Task 3: Retire the assumption in the prose, and record the reversal

Every comment in the tree that says "this is assumed and not enforced" is now wrong, and a stale comment claiming a gap is worse than none — it sends the next author to close something already closed.

**Files:**
- Modify: `src/redstring/ports/chunk_store.py` (the `upsert_many` docstring)
- Modify: `src/redstring/chunks/adapters/postgres.py` (`_TERMS_ON_CONFLICT` and `_ON_CONFLICT` docstrings)
- Modify: `src/redstring/domain/chunk.py` (module docstring)
- Create: `docs/adr/0044-a-chunk-id-is-derived-not-supplied.md`
- Modify: `docs/adr/index.md`
- Modify: `BACKLOG.md` (delete the B97 entry)
- Test: `tests/unit/domain/test_chunk.py` (no new tests; this task is prose)

**Interfaces:**
- Consumes: `StoredChunk.id` as a computed field, from Task 2.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Update the port docstring**

In `src/redstring/ports/chunk_store.py`, `ChunkWriter.upsert_many` currently ends its identity paragraph with a reference to B97 as an open gap. Replace that paragraph with:

```
        A chunk id is content-addressed over `(source_id, text)` and is
        derived by `StoredChunk` rather than supplied, so a write reusing an
        id is necessarily writing the same text. Both adapters rely on that
        to skip re-deriving `doc_length`, the term index and `embedding` on
        conflict; it is a property of the type, not a rule a caller can
        break. See `docs/adr/0044-a-chunk-id-is-derived-not-supplied.md`.
```

- [ ] **Step 2: Update the two adapter constants**

In `src/redstring/chunks/adapters/postgres.py`:

- `_TERMS_ON_CONFLICT`'s docstring says ids are content-addressed "see the module docstring's identity discussion". Change that clause to say the id is *derived by the type*, so the "can never legitimately change" claim is now proved rather than assumed.
- `_ON_CONFLICT`'s docstring contains the parenthetical "(BACKLOG B97 tracks that this reasoning is not yet enforced by a guard)". Delete that parenthetical and replace it with "(`StoredChunk.id` is a computed field, so this reasoning is enforced by construction — ADR 0044)".

- [ ] **Step 3: Update the domain module docstring**

`src/redstring/domain/chunk.py`'s "## Identity is content-addressed" section describes `chunk_id` as what a caller uses. Add a closing sentence: "`StoredChunk` derives its own `id` from these two fields and accepts no other value, so this is a property of the type rather than a convention callers follow."

- [ ] **Step 4: Write the ADR**

Read two existing ADRs first for the house form — `docs/adr/0038-the-chunks-vector-lives-on-the-chunk.md` (same subsystem) and `docs/adr/0042-themes-are-recomputed-never-stored.md`. Match their headings and their habit of recording what was rejected and why.

Create `docs/adr/0044-a-chunk-id-is-derived-not-supplied.md` covering:
- **Context:** three columns are skipped on conflict because a content-addressed id fixes the text; `StoredChunk.id` was a caller-supplied `str`, so the two adapters could rank the same id over different text (B97).
- **Decision:** `id` becomes a `computed_field`; `extra="forbid"` makes a stale `id=` loud.
- **Rejected:** last-write-wins on derived state — contradicts the shipped design and needs an unsafe same-statement DELETE-then-INSERT of term rows; a validator rather than a computed field — still makes every caller compute the value and only tells them afterwards; a compliance case asserting the adapters agree — tests a state that can no longer be built.
- **Consequences:** breaking for callers with self-assigned ids; `model_dump()` shape unchanged; a legacy row with a non-derived id reads back under its derived id.

- [ ] **Step 5: Add the ADR to the index**

Append a row to `docs/adr/index.md` matching the format of the existing entries.

- [ ] **Step 6: Delete the B97 entry**

In `BACKLOG.md`, delete the whole `### B97. Same chunk id, changed text diverges between the adapters` section. Then:
- Section 1's opening paragraph names B97 in its ordering rationale ("B97 needs a caller who declines the content-addressed id scheme"). Remove that clause and leave the other three.
- `grep -rn "B97" src/ tests/ docs/ BACKLOG.md` and fix every remaining reference. The "How to read this file" section keeps a table of deleted entries and where their lesson went — add a row for B97 pointing at ADR 0044.

- [ ] **Step 7: Verify no stale references remain**

Run: `grep -rn "B97" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=docs/superpowers`
Expected: only the BACKLOG "deleted entries" table row and the ADR.

- [ ] **Step 8: Run the domain and chunk tests once more**

Run: `uv run pytest tests/unit/domain/test_chunk.py tests/unit/chunks/ tests/unit/ports/ -q -p no:randomly`
Expected: PASS. (`tests/unit/ports/` may not exist; drop it from the command if so.)

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "Record that a chunk id is derived, and close B97"
```
