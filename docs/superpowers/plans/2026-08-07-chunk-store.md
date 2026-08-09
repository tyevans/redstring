# Chunk Corpus (B1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store the passages a document was split into, tenant-scoped and
content-addressed, linked to the entities extracted from them, written by a
projection off a new event that two write paths emit.

**Architecture:** A `StoredChunk` domain type; a `ChunkStore` port with no
search method; in-memory and Postgres adapters behind a shared compliance
suite; a `DocumentChunked` event folded by `ChunkProjection` through a single
`replace_source` call; emitted both by the extraction pipeline (with entity
links) and by a new `composition/index_documents.py` (without).

**Tech Stack:** Python 3.12+, pydantic v2, `eventsource`, asyncpg, pytest +
hypothesis, `uv`.

**Spec:** `docs/superpowers/specs/2026-08-07-chunk-store-design.md` — read it
if a task's reasoning is unclear, but the tasks below are self-contained.

## Global Constraints

- **Do not bump the version.** `pyproject.toml` stays at its current version.
- **Quality gates run on `git commit`.** Never run `ruff`, `bandit`,
  `lint-imports`, `mypy` or `pytest` as separate pre-commit steps. Write the
  change, then commit; the hook fixes what it can (re-`git add` and commit
  again when it does).
- **Never edit `pyproject.toml` dependency tables by hand.** Use `uv add`.
  After any dependency change, re-sync with `uv sync --all-extras`.
- **Anything noticed and not fixed goes in `BACKLOG.md` in the same commit
  that passes it by.** Name the file and line, say what is actually wrong, and
  say what you learned that made you defer rather than fix.
- **`mypy --strict` covers every module.** There is no exclude list and none
  may be added.
- **Every module starts with `from __future__ import annotations`.**
- Commit messages: imperative, sentence case, no trailing period, no
  `feat:`/`fix:` prefix. Body carries counts and file tables; the ADR and
  docs never do.
- **The term "BM25" appears nowhere under `src/`.** It belongs in ADR 0022 and
  in this spec's ADR. B1 builds no ranker.
- **`entity_ids` empty means no entities were extracted from this passage, not
  that extraction is pending.** That sentence, or one materially identical to
  it, appears on the `StoredChunk` docstring.
- **ADR numbers are allocated at merge, not at drafting.** Draft as
  `docs/adr/00XX-the-chunk-corpus.md` and renumber in the final task against
  `git ls-tree --name-only main docs/adr/ | sort | tail -1`. Renumbering means
  the filename, the H1, **and** every inbound citation.

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/redstring/domain/chunk.py` | `ChunkId`, `StoredChunk`, `chunk_id()` |
| `src/redstring/ports/chunk_store.py` | the `ChunkStore` Protocol |
| `src/redstring/chunks/__init__.py` | new sibling-band package |
| `src/redstring/chunks/adapters/memory.py` | `InMemoryChunkStore` |
| `src/redstring/chunks/adapters/postgres.py` | `PostgresChunkStore` |
| `src/redstring/events/document.py` | `DocumentChunked` (modify) |
| `src/redstring/aggregates/document.py` | `record_chunking` (modify) |
| `src/redstring/projections/chunk.py` | `ChunkProjection` |
| `src/redstring/composition/index_documents.py` | the LLM-free write path |
| `src/redstring/testing/chunk_store.py` | shared suite, not collected directly |
| `tests/unit/chunks/test_compliance_coverage.py` | introspection gate |

---

### Task 1: The domain type and content-addressed identity

**Files:**
- Create: `src/redstring/domain/chunk.py`
- Test: `tests/unit/domain/test_chunk.py`

**Interfaces:**
- Consumes: `TenantId`, `SourceId`, `EntityId` from `domain/ids.py`;
  `reject_unstorable_text` from `domain/json_safety.py`.
- Produces: `ChunkId = str`, `StoredChunk`, `chunk_id(source_id, text)`.
  Every later task uses all three.

- [ ] **Step 1: Write the failing tests**

```python
"""`StoredChunk` and the content-addressed id."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from redstring.domain.chunk import StoredChunk, chunk_id


def test_the_same_text_under_the_same_source_gets_the_same_id() -> None:
    assert chunk_id("doc-1", "Ada Lovelace wrote the first algorithm.") == chunk_id(
        "doc-1", "Ada Lovelace wrote the first algorithm."
    )


def test_the_same_text_under_a_different_source_gets_a_different_id() -> None:
    """Identity includes provenance.

    Boilerplate shared by two documents is two chunks. Were it one, one
    document's `entity_ids` would attach to the other's passage and the two
    would fight over the same row on every replay.
    """
    assert chunk_id("doc-1", "All rights reserved.") != chunk_id("doc-2", "All rights reserved.")


def test_text_differing_only_in_whitespace_gets_a_different_id() -> None:
    """No normalisation. Two passages with different offsets are two passages."""
    assert chunk_id("doc-1", "Ada  Lovelace") != chunk_id("doc-1", "Ada Lovelace")


def test_the_source_and_the_text_cannot_be_confused_for_one_another() -> None:
    """The delimiter is load-bearing.

    A naive `hash(source_id + text)` makes ("ab", "c") and ("a", "bc") the
    same chunk. They are different chunks of different documents.
    """
    assert chunk_id("ab", "c") != chunk_id("a", "bc")


def test_a_chunk_defaults_to_no_entities_and_no_metadata() -> None:
    """Built directly, not through a factory, so the defaults actually run."""
    chunk = StoredChunk(
        id=chunk_id("doc-1", "text"),
        tenant_id=uuid4(),
        source_id="doc-1",
        text="text",
        chunk_index=0,
        start_char=0,
        end_char=4,
    )
    assert chunk.entity_ids == []
    assert chunk.metadata == {}


def test_two_chunks_do_not_share_a_default_entity_list() -> None:
    """A mutable default shared between instances is the classic pydantic trap."""
    tenant = uuid4()
    first = StoredChunk(
        id="a",
        tenant_id=tenant,
        source_id="d",
        text="t",
        chunk_index=0,
        start_char=0,
        end_char=1,
    )
    second = StoredChunk(
        id="b",
        tenant_id=tenant,
        source_id="d",
        text="t",
        chunk_index=1,
        start_char=1,
        end_char=2,
    )
    first.entity_ids.append(uuid4())
    assert second.entity_ids == []


def test_a_nul_byte_in_the_text_is_rejected() -> None:
    """Postgres rejects it at INSERT; the boundary is where it should fail.

    Not hypothetical -- it arrives from PDF text extraction.
    """
    with pytest.raises(ValidationError):
        StoredChunk(
            id="a",
            tenant_id=uuid4(),
            source_id="d",
            text="bad\x00text",
            chunk_index=0,
            start_char=0,
            end_char=8,
        )


def test_a_nul_byte_in_the_metadata_is_rejected() -> None:
    with pytest.raises(ValidationError):
        StoredChunk(
            id="a",
            tenant_id=uuid4(),
            source_id="d",
            text="fine",
            chunk_index=0,
            start_char=0,
            end_char=4,
            metadata={"note": "bad\x00"},
        )


def test_entity_ids_survive_a_round_trip_as_uuids() -> None:
    entity = uuid4()
    chunk = StoredChunk(
        id="a",
        tenant_id=uuid4(),
        source_id="d",
        text="t",
        chunk_index=0,
        start_char=0,
        end_char=1,
        entity_ids=[entity],
    )
    restored = StoredChunk.model_validate(chunk.model_dump(mode="json"))
    assert restored.entity_ids == [entity]
    assert isinstance(restored.entity_ids[0], UUID)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/domain/test_chunk.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'redstring.domain.chunk'`

- [ ] **Step 3: Write the implementation**

```python
"""One stored passage of a document, and how it is identified.

## This is not `extraction.chunking.Chunk`

That type is a dataclass in the extraction layer describing a split in
progress: transient, tenantless, consumed within a single pipeline run. This
one is a stored record. They share four field names and no lifetime, and a
shared base class would put the extraction layer's type into the domain while
giving the transient one a tenant it has no way to fill.

## Identity is content-addressed

`chunk_id(source_id, text)` hashes the source id and the text exactly as
stored. Re-chunking a document under different settings therefore produces
genuinely new ids rather than overwriting old ones in place.

Positional identity -- `(source_id, chunk_index)` -- was rejected for that
reason. Under it, chunk 3 of a re-chunked document is a *different passage*
wearing the same id, so its stored entity links (and, once chunk embeddings
land, its stored vector) silently describe text that no longer says what they
claim. The cost of content addressing is orphans, and `ChunkStore.replace_source`
is where that cost is paid.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field, field_validator

from redstring.domain.ids import EntityId, SourceId, TenantId
from redstring.domain.json_safety import reject_unstorable_text

#: A chunk's identity: the hex digest produced by `chunk_id`.
ChunkId = str

#: Separates the source id from the text in the hashed preimage. A NUL cannot
#: occur in either -- `reject_unstorable_text` refuses it in the text, and a
#: `SourceId` carrying one could not be stored -- so no pair of inputs can
#: produce the same preimage as a different pair. Without a delimiter,
#: ("ab", "c") and ("a", "bc") would be one chunk.
_DELIMITER = b"\x00"


def chunk_id(source_id: SourceId, text: str) -> ChunkId:
    """The identity of `text` as a passage of `source_id`.

    The text is hashed **exactly as stored**, with no normalisation. Two
    passages differing only in whitespace have different `start_char`/
    `end_char`, so collapsing them would give one id two offsets -- and
    normalising here would create a second scheme to keep in step with the one
    in `extraction/mapping.py`.
    """
    digest = hashlib.sha256()
    digest.update(source_id.encode("utf-8"))
    digest.update(_DELIMITER)
    digest.update(text.encode("utf-8"))
    return digest.hexdigest()


class StoredChunk(BaseModel):
    """One passage of one document, under one tenant.

    `entity_ids` and `metadata` are mutable on purpose: a store handing back
    its own object would let a caller corrupt stored state, and the port
    requires that it does not. Immutable containers would make the compliance
    suite's mutation-isolation tests unfalsifiable -- they would pass on an
    adapter that leaks, because there would be nothing to mutate.

    **An empty `entity_ids` means no entities were extracted from this
    passage. It does not mean extraction is pending.** It is legitimately
    empty for every chunk written by `index_documents`, which never calls an
    LLM, so code reading emptiness as "not yet processed" is wrong forever and
    looks reasonable in review.
    """

    id: ChunkId
    tenant_id: TenantId
    source_id: SourceId
    text: str
    chunk_index: int
    start_char: int
    end_char: int
    entity_ids: list[EntityId] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def _text_is_storable(cls, value: str) -> str:
        reject_unstorable_text(value, what="text")
        return value

    @field_validator("metadata")
    @classmethod
    def _metadata_is_storable(cls, value: dict[str, Any]) -> dict[str, Any]:
        reject_unstorable_text(value, what="metadata")
        return value
```

Check `reject_unstorable_text`'s signature in
`src/redstring/domain/json_safety.py` before writing the validators — if it
does not accept a bare `str`, extend it there rather than writing a second
check here, and say so in the commit body.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/domain/test_chunk.py -v`
Expected: PASS

- [ ] **Step 5: Break it on purpose**

Delete `digest.update(_DELIMITER)` and confirm
`test_the_source_and_the_text_cannot_be_confused_for_one_another` goes red.
Restore it. Then change `entity_ids: list[EntityId] = Field(default_factory=list)`
to `= []` and confirm the tests still pass — pydantic v2 deep-copies literal
defaults, so this one is *not* a live trap here; note that in the commit body
rather than claiming the test caught something it cannot.

- [ ] **Step 6: Commit**

```bash
git add src/redstring/domain/chunk.py tests/unit/domain/test_chunk.py
git commit -m "Add the stored chunk and its content-addressed id"
```

---

### Task 2: The `ChunkStore` port

**Files:**
- Create: `src/redstring/ports/chunk_store.py`
- Test: none of its own — a Protocol has no behaviour. Task 4's compliance
  suite is this task's test, and Task 3 is its first implementation.

**Interfaces:**
- Consumes: `StoredChunk`, `ChunkId` from Task 1.
- Produces: `ChunkStore`, a `runtime_checkable` Protocol with exactly the six
  methods below. Every later task depends on these signatures verbatim.

- [ ] **Step 1: Write the port**

```python
"""The `ChunkStore` port: stored passages, in domain terms.

Like `GraphStore` and `VectorStore`, a `ChunkStore` is a **projection**. The
event log is the authority; every write here is idempotent because projection
handlers replay.

Every method is tenant-scoped. There is no cross-tenant read, ever.

## There is no search method, and that is deliberate

Retrieval over this corpus -- embeddings, a term-weighted ranker, a public
result type -- is a separate piece of work with its own design. Every one of
its decisions is downstream of what a stored chunk *is*, and guessing a search
signature before the corpus exists is how a port acquires a method its
adapters cannot implement the same way. Adding a method to our own port later
costs nothing.

## `replace_source` is one operation, not an upsert and a delete

Folding one `DocumentChunked` event must be atomic. Split into an
`upsert_many` followed by a `delete`, a crash between them leaves a corpus
that is neither the old chunking nor the new one -- and once term statistics
are computed over it, leaves them computed over a set that never existed.

An empty `chunks` argument is legal and means "this source now has no
chunks". It is not a no-op guard.

## `chunk_index` is not unique, so ordering needs a tie-break

Content-addressed ids mean a re-chunk landing mid-replay can transiently
produce two chunks claiming index 3. `get_by_source` therefore orders by
`chunk_index` ascending **and then by `id` ascending**; ordering on the index
alone would let two adapters disagree about which comes first, which is
exactly the divergence the compliance suite exists to prevent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.chunk import ChunkId, StoredChunk
    from redstring.domain.ids import SourceId, TenantId


@runtime_checkable
class ChunkStore(Protocol):
    """Storage for the passages a document was split into."""

    async def upsert_many(self, chunks: Sequence[StoredChunk]) -> None:
        """Insert or replace chunks, keyed by `(tenant_id, id)`.

        Idempotent, last-write-wins. Chunks may belong to different tenants;
        each is keyed by its own `tenant_id`. Two chunks with the same
        `(tenant_id, id)` in one call leave one row holding the later value --
        the same rule that applies across calls.

        A document's chunking is thousands of rows, so an adapter over a
        database must send this as one statement, not a loop.
        """
        ...

    async def get(self, chunk_id: ChunkId, tenant_id: TenantId) -> StoredChunk | None:
        """Return the stored chunk, or `None` if this tenant has no such id.

        An unknown id is not an error. The returned chunk is the caller's:
        mutating it -- including appending to `entity_ids` -- cannot change
        stored state.
        """
        ...

    async def get_by_source(self, source_id: SourceId, tenant_id: TenantId) -> list[StoredChunk]:
        """This tenant's chunks of one source, ordered.

        Ordered by `chunk_index` ascending, ties broken by `id` ascending; see
        the module docstring for why the tie-break is not optional. An unknown
        source yields `[]`. The returned chunks are the caller's.
        """
        ...

    async def replace_source(
        self,
        source_id: SourceId,
        tenant_id: TenantId,
        chunks: Sequence[StoredChunk],
    ) -> int:
        """Make `chunks` this source's whole chunking; return orphans removed.

        Writes every element and deletes this tenant's chunks of `source_id`
        that are absent from it, as one operation. The return value counts
        only the deletions, so a plain re-delivery of the same event returns
        `0` while a genuine re-chunk returns however many passages the new
        settings replaced.

        Every element must carry this `source_id` and `tenant_id`; a mismatch
        raises `ValueError` rather than being written under the argument's
        values, because silently rewriting a chunk's provenance is how one
        document's entity links end up on another's passage.

        An empty `chunks` empties the source. That is legal.
        """
        ...

    async def delete_by_source(self, source_id: SourceId, tenant_id: TenantId) -> int:
        """Delete every chunk of one source; return how many were removed.

        Idempotent: an unknown source returns `0` rather than raising, so
        replaying a delete is not an error.
        """
        ...

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        """Delete every chunk of `tenant_id`; return how many were removed.

        No other tenant is touched.
        """
        ...
```

- [ ] **Step 2: Verify it type-checks and imports**

Run: `uv run python -c "from redstring.ports.chunk_store import ChunkStore; print(ChunkStore)"`
Expected: prints the Protocol.

- [ ] **Step 3: Commit**

```bash
git add src/redstring/ports/chunk_store.py
git commit -m "Add the ChunkStore port, with no search method"
```

---

### Task 3: The in-memory adapter, and placing `chunks` on the layer contract

**Files:**
- Create: `src/redstring/chunks/__init__.py`,
  `src/redstring/chunks/adapters/__init__.py`,
  `src/redstring/chunks/adapters/memory.py`
- Modify: `pyproject.toml` (the `lint-imports` layer list and its inline
  reasoning), `CLAUDE.md` (the layer diagram in "Architecture contract")
- Test: `tests/unit/chunks/__init__.py`, `tests/unit/chunks/test_memory_store.py`

**Interfaces:**
- Consumes: `ChunkStore` (Task 2), `StoredChunk` (Task 1).
- Produces: `InMemoryChunkStore()` — no constructor arguments.

**Critical:** `containers = ["redstring"]` with `exhaustive = true` means the
new top-level package **fails the import contract until it is placed**. The
`pyproject.toml` and `CLAUDE.md` edits are part of this task, not a follow-up;
the commit will not pass the gate without them. Place `chunks` in the sibling
band beside `graph` and `vector` — it holds a projection target, needs nothing
from either, and neither needs anything from it.

Model the adapter on `src/redstring/vector/adapters/memory.py`: copy-on-write
and copy-on-read via `model_copy(deep=True)` in both directions, and the same
"validate every element before writing any" discipline in `upsert_many`.

- [ ] **Step 1: Write the failing tests**

Only the tests true of *this* adapter and no other go in this file; everything
about the contract goes in Task 4's shared suite. Follow
`tests/unit/vector/test_memory_store.py`'s shape.

```python
"""`InMemoryChunkStore`: the tests true of this adapter and no other."""

from __future__ import annotations

from uuid import uuid4

import pytest

from redstring.chunks.adapters.memory import InMemoryChunkStore
from redstring.domain.chunk import StoredChunk


@pytest.mark.unit
async def test_a_fresh_store_holds_nothing() -> None:
    store = InMemoryChunkStore()
    assert await store.get_by_source("doc-1", uuid4()) == []


@pytest.mark.unit
async def test_it_holds_no_state_outside_itself() -> None:
    """Two stores are independent; nothing is class-level or module-level."""
    tenant = uuid4()
    first, second = InMemoryChunkStore(), InMemoryChunkStore()
    await first.upsert_many(
        [
            StoredChunk(
                id="a",
                tenant_id=tenant,
                source_id="doc-1",
                text="t",
                chunk_index=0,
                start_char=0,
                end_char=1,
            )
        ]
    )
    assert await second.get("a", tenant) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/chunks/ -v`
Expected: FAIL — `ModuleNotFoundError: redstring.chunks`

- [ ] **Step 3: Write the adapter**

```python
"""In-memory `ChunkStore`: the reference adapter.

A real implementation, not a stub. It enforces every contract the port states
-- provenance validation on `replace_source`, the `(chunk_index, id)` ordering,
the orphan count -- because an adapter more permissive than its port is
useless as a reference: tests written against it would pass here and fail on
Postgres.

**Copy on write and on read**, as in `vector/adapters/memory.py`. Handing out
a reference lets a caller mutate stored state by accident, and keeping the
caller's object lets a caller mutate it afterwards. Both directions are closed
with a deep copy -- `entity_ids` is a list, so a shallow copy would leave it
shared.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redstring.domain.chunk import ChunkId, StoredChunk
    from redstring.domain.ids import SourceId, TenantId


class InMemoryChunkStore:
    """A `ChunkStore` backed by plain dictionaries."""

    def __init__(self) -> None:
        self._chunks: dict[TenantId, dict[ChunkId, StoredChunk]] = {}

    async def upsert_many(self, chunks: Sequence[StoredChunk]) -> None:
        for chunk in chunks:
            tenant = self._chunks.setdefault(chunk.tenant_id, {})
            # The key is the *pair*: `chunk.tenant_id` selects the mapping and
            # `chunk.id` the slot, so two tenants holding the same
            # content-addressed id are two rows. Content addressing makes that
            # collision ordinary rather than astronomically unlikely.
            tenant[chunk.id] = chunk.model_copy(deep=True)

    async def get(self, chunk_id: ChunkId, tenant_id: TenantId) -> StoredChunk | None:
        chunk = self._chunks.get(tenant_id, {}).get(chunk_id)
        return None if chunk is None else chunk.model_copy(deep=True)

    async def get_by_source(self, source_id: SourceId, tenant_id: TenantId) -> list[StoredChunk]:
        found = [
            chunk
            for chunk in self._chunks.get(tenant_id, {}).values()
            if chunk.source_id == source_id
        ]
        # `chunk_index` then `id`: the index is not unique under content
        # addressing, and ordering on it alone would let two adapters disagree.
        found.sort(key=lambda chunk: (chunk.chunk_index, chunk.id))
        return [chunk.model_copy(deep=True) for chunk in found]

    async def replace_source(
        self,
        source_id: SourceId,
        tenant_id: TenantId,
        chunks: Sequence[StoredChunk],
    ) -> int:
        strays = [
            chunk
            for chunk in chunks
            if chunk.source_id != source_id or chunk.tenant_id != tenant_id
        ]
        if strays:
            raise ValueError(
                f"every chunk must carry source_id={source_id!r} and "
                f"tenant_id={tenant_id}; found "
                f"{sorted({(c.source_id, str(c.tenant_id)) for c in strays})}"
            )

        keep = {chunk.id for chunk in chunks}
        tenant = self._chunks.setdefault(tenant_id, {})
        orphans = [
            chunk_id
            for chunk_id, chunk in tenant.items()
            if chunk.source_id == source_id and chunk_id not in keep
        ]
        for chunk_id in orphans:
            del tenant[chunk_id]
        await self.upsert_many(chunks)
        return len(orphans)

    async def delete_by_source(self, source_id: SourceId, tenant_id: TenantId) -> int:
        tenant = self._chunks.get(tenant_id, {})
        doomed = [chunk_id for chunk_id, chunk in tenant.items() if chunk.source_id == source_id]
        for chunk_id in doomed:
            del tenant[chunk_id]
        return len(doomed)

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        return len(self._chunks.pop(tenant_id, {}))
```

Note the validation is collected into a list rather than counted or
short-circuited: `.claude/rules/recurring-defects.md` and CLAUDE.md's
failure-shape table both call out that collecting the offending items beats
comparing lengths, and a `break` on the first stray would be identical to
`continue` on a one-element remainder.

- [ ] **Step 4: Place the package on the layer contract**

In `pyproject.toml`, add `redstring.chunks` to the sibling band alongside
`graph` and `vector`, and extend the inline reasoning with why it sits there:
it holds a projection target, needs nothing from `graph` or `vector`, and
neither needs anything from it; a caller joining a chunk to its entities holds
both ports, which is the same shape as every other cross-store question here.

In `CLAUDE.md`'s "Architecture contract", add `chunks` to the sibling line so
the diagram matches. A stale layer diagram in binding instructions sends the
next author to a package that does not exist.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/unit/chunks/ -v`
Expected: PASS

- [ ] **Step 6: Prove the contract bites**

Temporarily remove `redstring.chunks` from the layer list in `pyproject.toml`
and run `uv run lint-imports`. Expected: the contract fails on an unplaced
top-level package. Restore it. A gate you have never seen fail is not yet
evidence — record the observed failure text in the commit body.

- [ ] **Step 7: Commit**

```bash
git add src/redstring/chunks tests/unit/chunks pyproject.toml CLAUDE.md
git commit -m "Add the in-memory chunk store and place chunks on the contract"
```

---

### Task 4: The compliance suite and its coverage gate

**Files:**
- Create: `src/redstring/testing/chunk_store.py`,
  `tests/unit/chunks/test_compliance_coverage.py`
- Modify: `tests/unit/chunks/test_memory_store.py` (subclass the suite)

**Interfaces:**
- Consumes: `ChunkStore` (Task 2), `InMemoryChunkStore` (Task 3).
- Produces: `ChunkStoreCompliance`, which Task 5's Postgres adapter subclasses
  with one `new_store` hook.

Read `src/redstring/testing/vector_store.py` and
`tests/unit/vector/test_compliance_coverage.py` first — this task mirrors both.
The suite is **not** named `test_*.py`, so it is never collected directly; an
adapter opts in by subclassing and supplying `async def new_store()` returning
a fresh empty store.

The coverage gate derives read methods from the Protocol by introspection.
For `ChunkStore` that is `{"get", "get_by_source"}` — `replace_source`,
`delete_by_source` and `delete_by_tenant` return `int` and drop out
automatically. Assert that set explicitly, as the vector gate does: a coverage
checker over an empty set passes vacuously and is indistinguishable from a
working one. The domain-type set is `{StoredChunk}`. `ISOLATION_EXEMPT` is
`{}` and stays `{}`.

- [ ] **Step 1: Write the shared suite**

Required cases, each named against a row of CLAUDE.md's failure-shape table.
Write them with the conventional names where the gate requires it —
`test_get_returns_copies`, `test_get_never_crosses_tenants`,
`test_get_by_source_returns_copies`,
`test_get_by_source_never_crosses_tenants` — and the gate module needs no edit
at all.

```python
async def test_two_tenants_hold_the_same_chunk_id_independently(self) -> None:
    """The composite-key row, and the one this port is most exposed to.

    Content addressing makes this collision *ordinary*: the same passage of
    the same source id under two tenants hashes identically. A
    `(tenant_id, id)` key compared on `id` alone is a live defect here, not
    the astronomically-unlikely one `uuid4()` makes it elsewhere.
    """
    store = await self.new_store()
    left, right = uuid4(), uuid4()
    shared = chunk_id("doc-1", "shared passage")
    await store.upsert_many(
        [
            StoredChunk(
                id=shared,
                tenant_id=left,
                source_id="doc-1",
                text="shared passage",
                chunk_index=0,
                start_char=0,
                end_char=14,
                metadata={"owner": "left"},
            ),
            StoredChunk(
                id=shared,
                tenant_id=right,
                source_id="doc-1",
                text="shared passage",
                chunk_index=0,
                start_char=0,
                end_char=14,
                metadata={"owner": "right"},
            ),
        ]
    )
    assert (await store.get(shared, left)).metadata == {"owner": "left"}
    assert (await store.get(shared, right)).metadata == {"owner": "right"}
    assert await store.delete_by_tenant(left) == 1
    assert (await store.get(shared, right)).metadata == {"owner": "right"}


async def test_replace_source_removes_an_orphan_that_precedes_a_survivor(self) -> None:
    """The loop row.

    On a one-element remainder `break` and `continue` are the same function.
    The orphan must come *before* a chunk that survives, or an implementation
    that stops at the first deletion passes.
    """


async def test_replace_source_with_an_empty_set_empties_the_source(self) -> None:
    """`if not chunks: return 0` is the guard that looks defensive and is wrong."""


async def test_replace_source_writes_a_source_that_never_existed(self) -> None:
    """The fixture row: at least one path starts from genuinely nothing."""


async def test_replace_source_leaves_another_source_alone(self) -> None:
    """Two sources under one tenant; replacing one must not touch the other."""


async def test_replace_source_returns_the_orphan_count_not_the_write_count(self) -> None:
    """A counter needs a test asserting it non-zero *and* distinguishable.

    Replace two chunks with three, one of which is carried over: the answer is
    1, and it differs from every other count in the call (2 before, 3 after,
    2 written new). Four counters all summed to the same number cannot tell
    you which line was wired to which field.
    """


async def test_replace_source_rejects_a_chunk_from_another_source(self) -> None: ...
async def test_replace_source_rejects_a_chunk_from_another_tenant(self) -> None: ...


async def test_get_by_source_orders_two_chunks_sharing_an_index_by_id(self) -> None:
    """Ties that never coincide are the failure shape this repo hit twice.

    The tie-break exists for exactly this input, so a test must produce it:
    two chunks with `chunk_index=3` and ids that sort in a known order.
    """


async def test_get_returns_copies(self) -> None:
    """Mutate the result -- including appending to `entity_ids` -- and re-read.

    A shallow copy leaves `entity_ids` shared and passes every behavioural
    assertion, because handing back the stored object is correct on the read
    and wrong only afterwards.
    """


async def test_get_by_source_returns_copies(self) -> None: ...
async def test_get_never_crosses_tenants(self) -> None: ...
async def test_get_by_source_never_crosses_tenants(self) -> None: ...
async def test_delete_by_source_is_idempotent_on_an_unknown_source(self) -> None: ...
async def test_delete_by_tenant_touches_no_other_tenant(self) -> None: ...
async def test_upsert_many_is_last_write_wins_within_one_call(self) -> None: ...
```

Fill in every body — the ellipses above are a task list, not the deliverable.

- [ ] **Step 2: Write the coverage gate**

Copy `tests/unit/vector/test_compliance_coverage.py` and adapt: the
`_PORT_NAMESPACE` needs `StoredChunk`, `ChunkId`, `SourceId`, `TenantId`,
`Sequence`; the self-guard is `assert read_methods() == {"get", "get_by_source"}`.

- [ ] **Step 3: Opt the in-memory adapter in**

`class TestMemoryChunkStore(ChunkStoreCompliance)` with a three-line
`new_store`. Keep the two adapter-specific tests from Task 3.

- [ ] **Step 4: Run**

Run: `uv run pytest tests/unit/chunks/ -v`
Expected: PASS, with the suite's cases visible in the output.

- [ ] **Step 5: Break it on purpose — three times**

Each of these must turn a test red. If one does not, the test is the problem,
not the adapter:

1. In `get`, return the stored object instead of a deep copy →
   `test_get_returns_copies` red.
2. In `replace_source`, change the orphan comprehension to stop at the first
   match → the orphan-before-survivor test red.
3. In `get_by_source`, sort by `chunk.chunk_index` alone → the shared-index
   test red.

Then delete the `ISOLATION_EXEMPT` self-guard assertion in the gate and
confirm the gate still passes — proving the guard is what makes the gate
non-vacuous — and restore it.

- [ ] **Step 6: Commit**

```bash
git add src/redstring/testing/chunk_store.py tests/unit/chunks
git commit -m "Pin the ChunkStore contract in a shared suite with a coverage gate"
```

---

### Task 5: The Postgres adapter

**Files:**
- Create: `src/redstring/chunks/adapters/postgres.py`,
  `tests/integration/chunks/__init__.py`,
  `tests/integration/chunks/test_postgres_store.py`
- Modify: `tests/unit/test_dependencies_stay_confined.py` (a fifth row)

**Interfaces:**
- Consumes: `ChunkStore` (Task 2), `ChunkStoreCompliance` (Task 4).
- Produces: `PostgresChunkStore`, constructed and connected on the same shape
  as `PgVectorStore` — read `src/redstring/vector/adapters/pgvector.py` and
  follow its `connect`/`close`/`ensure_schema` structure, its guarded
  `import asyncpg` re-raised with the extra to install, and its `# nosec B608`
  convention on interpolated table names.

**The confinement row is not optional bookkeeping.** Three of the existing
four rows in `tests/unit/test_dependencies_stay_confined.py` were confined by
convention alone until slice 11, each correctly placed and each one commit
from not being. Add `asyncpg` confined to `chunks/adapters/`, guarded in both
directions so a row naming a directory that has stopped importing its library
fails rather than passing forever.

- [ ] **Step 1: Write the schema**

```sql
CREATE TABLE IF NOT EXISTS {table} (
    tenant_id   uuid    NOT NULL,
    id          text    NOT NULL,
    source_id   text    NOT NULL,
    text        text    NOT NULL,
    chunk_index integer NOT NULL,
    start_char  integer NOT NULL,
    end_char    integer NOT NULL,
    entity_ids  uuid[]  NOT NULL DEFAULT '{}',
    metadata    jsonb   NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS {table}_tenant_source_idx
    ON {table} (tenant_id, source_id, chunk_index, id);
```

The primary key is the **pair**. The index covers `get_by_source`'s filter and
its full ordering, so the ordering is served by the index rather than by a
sort — and it includes `id`, because the tie-break is part of the contract and
an index stopping at `chunk_index` would leave the tie resolved by whatever
the plan happened to do.

- [ ] **Step 2: Write `replace_source` as one statement**

This is the method the port's docstring is about; it must not be two round
trips. One statement, inside the adapter's transaction:

```sql
WITH incoming AS (
    SELECT * FROM unnest($3::text[], $4::text[], $5::integer[],
                         $6::integer[], $7::integer[], $8::jsonb[])
        AS t(id, text, chunk_index, start_char, end_char, metadata)
),
removed AS (
    DELETE FROM {table}
     WHERE tenant_id = $1 AND source_id = $2
       AND id <> ALL (SELECT id FROM incoming)
    RETURNING 1
),
written AS (
    INSERT INTO {table} (tenant_id, id, source_id, text, chunk_index,
                         start_char, end_char, entity_ids, metadata)
    SELECT $1, i.id, $2, i.text, i.chunk_index, i.start_char, i.end_char,
           e.ids, i.metadata
      FROM incoming i
      JOIN unnest($3::text[], $9::uuid[][]) AS e(id, ids) ON e.id = i.id
    ON CONFLICT (tenant_id, id) DO UPDATE SET
        source_id = EXCLUDED.source_id, text = EXCLUDED.text,
        chunk_index = EXCLUDED.chunk_index, start_char = EXCLUDED.start_char,
        end_char = EXCLUDED.end_char, entity_ids = EXCLUDED.entity_ids,
        metadata = EXCLUDED.metadata
    RETURNING 1
)
SELECT (SELECT count(*) FROM removed);
```

`entity_ids` is a `uuid[]` per row, so the parameter is an array of arrays;
asyncpg handles that, but **verify it against a real database before trusting
the shape** — if the nested-array binding is awkward, send the whole payload
as one `jsonb` parameter and unpack it with `jsonb_to_recordset` instead. Take
whichever works and say in the commit body which you took and why. Do not
fall back to a loop: the port's docstring forbids it.

Note the count is taken through a CTE rather than by parsing asyncpg's
`"DELETE n"` status string, matching `PgVectorStore.delete_by_tenant`.

Deduplicate the incoming chunks by `id` before binding, keeping the **last**
occurrence — `ON CONFLICT` cannot fire twice for the same row in one
statement, and last-write-wins within a call is the stated contract.
`PgVectorStore` has a `deduplicate` helper for exactly this; write the
equivalent rather than importing across sibling packages.

- [ ] **Step 3: Run the compliance suite against it**

```python
class TestPostgresChunkStore(ChunkStoreCompliance):
    async def new_store(self) -> ChunkStore: ...
    async def dispose(self, store: ChunkStore) -> None: ...
```

Mark the module `@pytest.mark.integration`. Follow
`tests/integration/vector/test_pgvector_store.py` for container setup and skip
conditions.

Run: `uv run pytest -m integration tests/integration/chunks/ -v`
Expected: PASS — the suite **unchanged**. If a case fails, fix the adapter. If
the port genuinely permits both behaviours, say so in the port and state the
weaker contract for everyone; editing the shared body to make one adapter pass
is the defect, not the fix.

- [ ] **Step 4: Add the confinement row and prove it bites**

Add the row, then add `import asyncpg` to `src/redstring/domain/chunk.py`,
confirm `tests/unit/test_dependencies_stay_confined.py` fails, and remove it.
Then delete the `chunks/adapters/` import from the adapter, confirm the row
fails in the other direction, and restore it.

- [ ] **Step 5: Commit**

```bash
git add src/redstring/chunks/adapters/postgres.py tests/integration/chunks \
        tests/unit/test_dependencies_stay_confined.py
git commit -m "Add the Postgres chunk store and confine asyncpg to its directory"
```

---

### Task 6: The event and the aggregate command

**Files:**
- Modify: `src/redstring/events/document.py`,
  `src/redstring/aggregates/document.py`
- Test: `tests/unit/events/test_document.py`,
  `tests/unit/aggregates/test_document.py` (extend both)

**Interfaces:**
- Consumes: `StoredChunk` (Task 1).
- Produces: `DocumentChunked(source_id, chunking_signature, chunks)` and
  `Document.record_chunking(...) -> DocumentChunked | None`. Tasks 7-9 use both.

**The idempotence key, pinned here so nobody guesses.** `Document` already
dedupes extraction on `model_version` and embedding on `embedding_model`,
in separate key spaces. Chunking gets a third: `chunking_signature`, a string
the emitter composes, and the two write paths compose it differently **on
purpose**:

- `index_documents` emits `f"{method}:{params_digest}"`.
- The extraction pipeline emits `f"{method}:{params_digest}:{model_version}"`.

That difference is what makes the common order work. Indexing a document and
later extracting it produces two different signatures, so both are recorded
and the extraction — which carries `entity_ids` — lands last and wins. A
retry of either is a no-op. Re-chunking under new settings changes
`params_digest` and is recorded.

The lossy case is the reverse order: extracting and *then* indexing the same
document replaces the chunk set with one carrying no entity links. That is
real behaviour, not an accident, and Task 9 documents it on `index_documents`
and tests it.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_chunked_event_rejects_a_chunk_from_another_tenant() -> None:
    """`_reject_foreign_tenants` -- the projection writes each chunk under its
    own tenant_id, so this is the one place the two can still be compared."""


def test_a_chunked_event_rejects_a_chunk_from_another_document() -> None:
    """Same rule the entity check enforces, and for the same reason."""


def test_a_chunked_event_accepts_an_empty_chunk_list() -> None:
    """A document that chunks to nothing is expressible; the projection needs
    it to empty a source."""


def test_recording_the_same_chunking_signature_twice_emits_nothing() -> None:
    """A retry after a crash is a no-op, matching record_extraction."""


def test_a_different_chunking_signature_is_recorded() -> None:
    """Re-chunking under new settings -- or extraction after indexing -- is a
    new fact, not a repeat."""


def test_chunking_and_extraction_keep_separate_key_spaces() -> None:
    """Recording a chunking under the string "v1" must not suppress an
    extraction under model_version "v1". The two namespaces overlap in
    practice, which is why embedding already has its own list."""
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/events/test_document.py tests/unit/aggregates/test_document.py -v`
Expected: FAIL — `ImportError: cannot import name 'DocumentChunked'`

- [ ] **Step 3: Add the event**

```python
@register_event
class DocumentChunked(TenantDomainEvent):
    """How one document was split, and into what.

    Carries the document's **whole** chunking, not one chunk, for the reason
    `DocumentExtracted` carries every entity: the projection folds it with one
    `replace_source` call, so a re-chunk is a replacement rather than an
    accumulation and an event is never partly applied. Split per chunk, the
    orphan deletion would have nothing to be scoped to.

    `chunking_signature` is what makes a repeat distinguishable from a new
    chunking; see `Document.record_chunking`.
    """

    event_version: int = 1
    aggregate_type: str = DOCUMENT_CATEGORY

    source_id: SourceId
    chunking_signature: str
    chunks: list[StoredChunk] = Field(default_factory=list)

    @model_validator(mode="after")
    def _chunks_belong_to_this_document_and_tenant(self) -> DocumentChunked:
        _reject_foreign_tenants(self, self.chunks, "chunks")
        strays = {c.source_id for c in self.chunks if c.source_id != self.source_id}
        if strays:
            raise ValueError(
                f"chunks must be attributed to the document they were split "
                f"from; found source_id {sorted(map(str, strays))} in an event "
                f"for {self.source_id!r}"
            )
        return self
```

`StoredChunk` has a `tenant_id` property, so it satisfies `_HasTenant`
structurally with no change to that Protocol — which is why `_HasTenant` is a
Protocol rather than a union.

- [ ] **Step 4: Add the aggregate command**

Add `chunking_signatures: list[str] = Field(default_factory=list)` to
`DocumentState`, a `record_chunking` mirroring `record_embeddings`, and an
`elif isinstance(event, DocumentChunked)` branch in `_apply`.

- [ ] **Step 5: Run**

Expected: PASS.

- [ ] **Step 6: Break it on purpose**

Change `_apply`'s new branch to append to `extraction_model_versions` instead
and confirm `test_chunking_and_extraction_keep_separate_key_spaces` goes red.
Restore.

- [ ] **Step 7: Commit**

```bash
git add src/redstring/events/document.py src/redstring/aggregates/document.py \
        tests/unit/events/test_document.py tests/unit/aggregates/test_document.py
git commit -m "Record how a document was chunked, keyed on a chunking signature"
```

---

### Task 7: The projection

**Files:**
- Create: `src/redstring/projections/chunk.py`
- Test: `tests/unit/projections/test_chunk.py`

**Interfaces:**
- Consumes: `DocumentChunked` (Task 6), `ChunkStore` (Task 2),
  `InMemoryChunkStore` (Task 3).
- Produces: `ChunkProjection(store)`, a `StoreProjection[ChunkStore]`.

Model on `src/redstring/projections/vector.py`, including
`_truncate_read_models` raising `NotImplementedError` with a message naming
`delete_by_tenant` — nothing here spans tenants.

**The replay tests need an oracle that is not the fold.** An equivalence whose
two sides both run the projection is preserved exactly by the bugs that drop
work: a handler that never deletes an orphan makes both sides agree on the
same wrong state. Build the expected corpus independently — a plain dict in
the test, keyed the way the port is — and assert against that.

- [ ] **Step 1: Write the failing tests**

```python
async def test_folding_one_event_writes_the_whole_chunking() -> None: ...


async def test_folding_the_same_event_twice_leaves_the_same_corpus() -> None:
    """Idempotent redelivery. Assert against an independently-built expectation,
    not against the result of the first fold."""


async def test_folding_a_re_chunk_removes_the_orphans() -> None:
    """The event carries the new chunking; the old passages must be gone.
    Assert the exact expected id set, built in the test."""


async def test_folding_an_empty_chunking_empties_the_source() -> None: ...


async def test_folding_two_sources_leaves_each_intact() -> None: ...


async def test_folding_two_tenants_leaves_each_intact() -> None:
    """Same source id, same content, two tenants -- so the same ChunkId."""


async def test_truncate_read_models_refuses_and_names_the_alternative() -> None: ...
```

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Write the projection**

```python
class ChunkProjection(StoreProjection[ChunkStore]):
    """Maintains a `ChunkStore` from the event log."""

    @handles(DocumentChunked)
    async def _apply_chunking(self, _context: object, event: DocumentChunked) -> None:
        await self._store.replace_source(event.source_id, event.tenant_id, event.chunks)
```

The whole handler is one call, which is the point of `replace_source` being
one port method: the fold is atomic, and a redelivered event produces the
identical corpus because the incoming set is the same set.

- [ ] **Step 4: Run** — Expected: PASS.

- [ ] **Step 5: Break it on purpose**

Replace the handler body with `await self._store.upsert_many(event.chunks)`.
`test_folding_a_re_chunk_removes_the_orphans` must go red. If it does not, the
test built its expectation from the fold and is worthless — fix the test
first, then restore the handler.

- [ ] **Step 6: Commit**

```bash
git add src/redstring/projections/chunk.py tests/unit/projections/test_chunk.py
git commit -m "Fold a document's chunking into the chunk store in one call"
```

---

### Task 8: Extraction emits the chunking, with entity links

**Files:**
- Modify: `src/redstring/extraction/pipeline.py`,
  `src/redstring/composition/build_graph.py`
- Test: `tests/unit/extraction/test_pipeline.py`,
  `tests/unit/composition/test_build_graph.py` (extend both)

**Interfaces:**
- Consumes: `DocumentChunked` (Task 6), `ChunkProjection` (Task 7),
  `chunk_id`/`StoredChunk` (Task 1).
- Produces: the extraction result gains the per-chunk payload; `build_graph`
  gains an optional `chunks: ChunkStore | None = None` parameter.

**Extraction still writes to no store.** It emits; the projection writes. The
pipeline's job here is to carry the chunk payload — and which entities each
chunk produced — out to whoever emits the event, exactly as it already carries
entities and relationships.

`extraction/merging.py` discards which chunk reported an entity once merging
has run. Capturing the link therefore happens **before** the merge, per chunk,
in the same place `mapping.py` turns extracted entities into ids. Read both
modules before starting; if the link cannot be captured without changing the
merge's signature, that is a real finding — say so rather than reshaping the
merge, and consider whether the pipeline should carry a
`dict[ChunkId, list[EntityId]]` alongside its existing result.

`chunks` on `build_graph` is optional and defaults to `None`, so every
existing caller is unaffected and the parameter's absence means "do not
maintain a corpus". A `None` store with a non-empty chunking is not an error.

- [ ] **Step 1: Write the failing tests**

```python
async def test_an_extracted_chunk_records_the_entities_it_produced() -> None:
    """Two chunks, each naming a different entity. Assert the *mapping*, not
    just that some chunk has some entities -- a payload that attached every
    entity to every chunk would pass the weaker assertion."""


async def test_a_chunk_that_produced_no_entities_records_an_empty_list() -> None:
    """A bad chunk followed by a good one, so `break` and `continue` differ."""


async def test_build_graph_without_a_chunk_store_still_builds_the_graph() -> None: ...


async def test_build_graph_with_a_chunk_store_populates_it() -> None:
    """End to end: the corpus holds the passages, and the passages hold the
    entity ids the graph store holds."""
```

- [ ] **Step 2-4: Run red, implement, run green.**

- [ ] **Step 5: Break it on purpose**

Attach every entity to every chunk and confirm the mapping assertion goes red.

- [ ] **Step 6: Commit**

```bash
git commit -m "Carry each chunk's entities out of extraction and into the corpus"
```

---

### Task 9: `index_documents`, the LLM-free write path

**Files:**
- Create: `src/redstring/composition/index_documents.py`
- Modify: `src/redstring/composition/__init__.py`
- Test: `tests/unit/composition/test_index_documents.py`

**Interfaces:**
- Consumes: `Chunker` from `extraction/protocols.py`, `ChunkProjection`
  (Task 7), `DocumentChunked` (Task 6), `SourceDocument`.
- Produces: `index_documents(...) -> IndexReport`, both exported in Task 10.

**Why this belongs in `composition`.** The layer's rule is that a module names
the pair of mutually-forbidden layers it joins. This one joins `extraction`
(the chunkers) and `projections` — the same pair `build_graph` names, so the
rule is satisfied by the argument already recorded in `pyproject.toml` rather
than needing a new one. State that in the module docstring.

`composition/__init__.py` currently re-exports six names. Add the new ones and
**verify every name anything imports still resolves** — `redstring/__init__.py`
imports from this package, and an incomplete re-export list breaks it at
import time.

- [ ] **Step 1: Write the failing tests**

```python
async def test_indexing_a_document_stores_its_passages_without_an_llm() -> None:
    """No LlmProvider is supplied at all -- not a fake, none. If the signature
    requires one, the path is not LLM-free."""


async def test_indexed_chunks_carry_no_entity_ids() -> None: ...


async def test_indexing_the_same_document_twice_is_a_no_op() -> None:
    """Same chunker settings, same signature, nothing emitted the second time."""


async def test_re_indexing_with_different_settings_replaces_the_passages() -> None: ...


async def test_indexing_after_extracting_discards_the_entity_links() -> None:
    """Documented behaviour, not an accident, and asserted so a change to it
    is a visible decision. The reverse order -- index then extract -- is the
    one that preserves them, and is tested beside it."""


async def test_extracting_after_indexing_preserves_the_entity_links() -> None: ...


async def test_the_report_counts_documents_and_chunks_separately() -> None:
    """Two documents chunking to three and five passages: 2 and 8, and the two
    numbers must differ, or a report wiring both fields to one count passes."""
```

- [ ] **Step 2-4: Run red, implement, run green.**

`IndexReport` carries at minimum `documents_indexed` and `chunks_written`.
Every counter gets a test asserting it non-zero under the condition it counts
and differing from its siblings.

- [ ] **Step 5: Break it on purpose**

Wire `chunks_written` to `documents_indexed` and confirm the report test goes
red.

- [ ] **Step 6: Commit**

```bash
git commit -m "Add index_documents, a chunking write path with no LLM call"
```

---

### Task 10: Public surface, ADR, docs, and the backlog

**Files:**
- Modify: `src/redstring/__init__.py`, `mkdocs.yml`, `BACKLOG.md`,
  `docs/adr/0022-the-lexical-channel-is-not-bm25.md` (Status only)
- Create: `docs/adr/00XX-the-chunk-corpus.md`,
  `docs/how-to/index-documents.md`
- Modify: the end-to-end example, to gain an indexing step

**Interfaces:** consumes everything above.

- [ ] **Step 1: Export the surface**

Add `StoredChunk`, `ChunkStore`, `ChunkProjection`, `InMemoryChunkStore`,
`index_documents` and `IndexReport` to `redstring.__all__`.

Three gates hold this honest, each blind to what the others catch. Expect the
**signature gate** to demand the closure: every type named in an exported
signature must itself be exported. `StoredChunk` names `TenantId`, `SourceId`
and `EntityId`, all exported already; `index_documents` will name whatever
chunker and event-store types its signature takes, and those may not be. If
exporting one obliges another, export it — the gate makes the closure visible
at the moment it happens, which is the point of it. And the gate walks the
MRO, so `ChunkProjection`'s constructor is `StoreProjection`'s.

Run the public-surface tests and let them tell you the closure rather than
guessing it.

- [ ] **Step 2: Write the ADR**

Draft as `docs/adr/00XX-the-chunk-corpus.md`. It records:

- The separation of "never fetches" from "never stores", and that the second
  was a description of what had been built rather than a decision anyone made.
- Content-addressed identity, with **positional recorded as rejected** and why.
- `replace_source` as one operation, with the split version recorded as
  rejected.
- The chunking signature, and why the two write paths compose it differently.
- The consequence: a corpus now exists, so a term-weighted ranker over it is
  possible. B1 does not build one.

Run it against the existing ADRs and say for each related one whether it
**stands**, is **amended**, or is **superseded**:

- `0022` — **amended.** Its premise ("this library stores no text") no longer
  holds; its *decision* stands unchanged, because the entity-name lexical
  channel is still not a term-weighted ranker and still catches `Acme Corp`.
  Add an "Amended by" pointer to `0022`'s **Status** section and touch nothing
  else in it — an ADR body is an immutable record.
- `0002` — say whether "two store ports" is superseded by a third. Argue it
  rather than asserting it: `0002` is about the *graph and vector* pair and
  the absence of `delete_entity`, and a chunk store may be orthogonal to what
  it decided.
- `0001` (event log schema and granularity) — a new event at document
  granularity; say whether it stands.
- `0007` and `0021` (composition's membership) — `index_documents` is a third
  module in the top layer; say which governs and whether the pair-naming rule
  is satisfied.
- `0006` (the public surface is gated) — stands; the new exports go through
  the same three gates.

**No counts, no file tables in the ADR body.** Those go in the commit message.

- [ ] **Step 3: Allocate the number**

```bash
git ls-tree --name-only main docs/adr/ | sort | tail -1
```

Number against that, **at merge time, not now**. Renumbering means the
filename, the H1, *and* every inbound citation. Add the page to `mkdocs.yml`'s
nav — `mkdocs build --strict` fails on a link to a missing page, and that gate
is the only thing that makes a half-finished renumber impossible to land.
(ADR 0020 was missing from the nav until the retrieval work found it
incidentally; check the new one is there and that nothing else has gone
missing.)

- [ ] **Step 4: Write the how-to and extend the example**

`docs/how-to/index-documents.md`: indexing without extraction, what
`entity_ids` being empty means, and the extract-then-index lossy case stated
plainly. The end-to-end example gains an indexing step **importing nothing but
`redstring`** — that is the third public-surface gate, and without it the
example could reach into an adapter module and pass while the surface is
empty.

- [ ] **Step 5: File the backlog**

At minimum:

- No chunk embeddings and no ranker — B2, with what B1 decided that B2 inherits.
- Whatever the Postgres `replace_source` binding experiment cost, if the
  nested-array form was abandoned.
- Anything else noticed and passed by.

- [ ] **Step 6: Run the whole gate**

```bash
uv run pytest && uv run mkdocs build --strict
```

- [ ] **Step 7: Commit**

```bash
git commit -m "Export the chunk corpus and record the decision that built it"
```

---

## Self-Review Notes

Checked against the spec: every section has a task. The one thing the spec
left underspecified — the aggregate's idempotence key — is pinned in Task 6,
including why the two write paths compose the signature differently, because
an implementer guessing "dedupe on the chunking signature" symmetrically would
have made the common index-then-extract order silently drop every entity link.

Signatures used in later tasks match the ones defined in Task 2. `chunk_id`,
`StoredChunk`, `ChunkStore`, `DocumentChunked`, `ChunkProjection` and
`index_documents` are spelled identically throughout.

Two places deliberately do not carry finished code, and say what to do
instead: the Postgres `entity_ids` array binding (verify against a real
database, take the alternative if the nested form is awkward, record which)
and the extraction-side capture of chunk→entity links (read `merging.py` and
`mapping.py` first; report rather than reshape the merge). Both are places
where writing plausible code into the plan would have been worse than naming
the uncertainty.
