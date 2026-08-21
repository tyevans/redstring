"""Postgres `ChunkStore`: the second adapter, and the test of the port.

Modelled on `redstring.vector.adapters.pgvector` -- asyncpg directly with no
ORM, a guarded import naming the extra to install, an interpolated table name
proved to be a bare identifier first, and delete counts taken through a CTE
rather than by parsing asyncpg's `"DELETE n"` status string.

## `chunk_index` is an `integer` column, and that is not incidental

Under a `text` column every query still answers, every write still round
trips, and `get_by_source` returns chunk 10 before chunk 2 -- a silently
reordered document. The compliance suite has one case using index 10 for
exactly this; on single digits a lexical sort and a numeric one are the same
function, so nothing else in the suite can tell the two schemas apart.

## `replace_source` sends its payload as one `jsonb` parameter

The obvious binding is parallel arrays through `unnest`, one per column. It
does not survive contact with `entity_ids`, which is a `uuid[]` *per row* and
therefore a nested array -- and **Postgres arrays are rectangular**, so asyncpg
rejects a list of unequal-length lists outright:

    DataError: invalid input for query argument $1: [[...]] (non-homogeneous array)

Chunks have different numbers of entities by construction, so the nested-array
form is not awkward here, it is unusable. The whole batch therefore travels as
a single `jsonb` document and is unpacked with `jsonb_to_recordset`, which
casts a JSON array of uuid strings straight into `uuid[]`.

That is one statement, which is the point: a `DocumentChunked` fold that
became an upsert *and* a delete would leave, on a crash between them, a corpus
that is neither the old chunking nor the new one. The port's docstring forbids
the loop for the same reason.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import TYPE_CHECKING, Any, Self

from redstring.chunks.provenance import reject_foreign_chunks
from redstring.domain.bm25 import CorpusStats
from redstring.domain.chunk import StoredChunk
from redstring.domain.chunk_ranking import LexicalCandidate, LexicalCandidates
from redstring.domain.chunk_retrieval import SemanticCandidate
from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.tokenize import tokenize
from redstring.domain.vector import clamp_score, has_zero_norm

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    import asyncpg

    from redstring.domain.chunk import ChunkId
    from redstring.domain.ids import EntityId, SourceId, TenantId

#: Table names are interpolated into SQL -- Postgres has no parameter form for
#: an identifier -- so the name is proved to be a bare lowercase identifier
#: first, exactly as `PgVectorStore` does. Anything else, including a quoted or
#: schema-qualified name, is rejected rather than escaped.
#:
#: This guard is what the `# nosec B608` markers below rest on: bandit sees an
#: f-string in a SQL literal and cannot see that the only interpolated value
#: was proved safe in `__init__`, nor that every caller-supplied value travels
#: as a `$n` parameter.
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

#: The record shape `jsonb_to_recordset` unpacks a payload into. Written once
#: because `upsert_many` and `replace_source` must agree about it: a column
#: typed differently in the two statements is the silent-divergence shape
#: inside a single adapter.
#:
#: `doc_length` and `embedding` are here and in `_COLUMNS` below, but
#: deliberately **not** in `_ON_CONFLICT` -- see that constant's docstring.
#: `embedding` is typed as the unconstrained `vector` rather than `vector(n)`:
#: `jsonb_to_recordset`'s `AS` clause names a type for the input function to
#: parse text into, and the actual column it lands in -- `vector({dimension})`
#: -- is what enforces the width, exactly as a plain `integer` value narrows
#: to a `smallint` column without the record shape naming that width either.
_INCOMING = (
    "t(tenant_id uuid, id text, source_id text, text text, chunk_index integer, "
    "start_char integer, end_char integer, entity_ids uuid[], metadata jsonb, "
    "doc_length integer, embedding vector)"
)

#: The record shape the term-index payload unpacks into. A term row has no
#: identity of its own beyond its key, so unlike `_INCOMING` there is no
#: matching `_ON_CONFLICT`: see `_TERMS_ON_CONFLICT`.
_TERMS_INCOMING = "t(tenant_id uuid, chunk_id text, term text, tf integer)"

#: Term rows are written `ON CONFLICT DO NOTHING`, never `DO UPDATE`. Chunk
#: ids are content-addressed over `(source_id, text)` and derived by the type
#: -- see `StoredChunk.id` -- so a given id always has the same text,
#: therefore always the same terms and the same `tf` for each. There is no
#: update path for a term row: the "obvious" DELETE-then-INSERT is both
#: unnecessary (the row can never legitimately change) and unsafe in one
#: statement, since a row deleted and reinserted by the same statement is a
#: same-statement double modification.
_TERMS_ON_CONFLICT = "ON CONFLICT (tenant_id, chunk_id, term) DO NOTHING"

#: What every write sets when the key already exists. Last-write-wins, and the
#: list is the whole row bar the key -- an omitted column here is a field one
#: adapter preserves and the other drops, which is `recurring-defects.md` §1's
#: third observed shape verbatim.
#:
#: `doc_length` and `embedding` are deliberately **absent** from this list,
#: unlike every other column in `_COLUMNS`. Neither is an oversight of that
#: rule: `doc_length` is a pure function of `text`, and `embedding` is written
#: once and never updated on conflict either -- a content-addressed id fixes
#: `text` for good, so there is no value either column could ever need
#: updating to (`StoredChunk.id` is a computed field, so this reasoning is
#: enforced by construction -- ADR 0044). Including them in the `SET` list
#: would be a no-op that reads as one more ordinary column and hides the
#: reasoning; omitting them and saying so here is the honest spelling.
_ON_CONFLICT = (
    "ON CONFLICT (tenant_id, id) DO UPDATE SET "
    "source_id = EXCLUDED.source_id, text = EXCLUDED.text, "
    "chunk_index = EXCLUDED.chunk_index, start_char = EXCLUDED.start_char, "
    "end_char = EXCLUDED.end_char, entity_ids = EXCLUDED.entity_ids, "
    "metadata = EXCLUDED.metadata"
)

_COLUMNS = (
    "tenant_id, id, source_id, text, chunk_index, start_char, end_char, "
    "entity_ids, metadata, doc_length, embedding"
)

#: `_COLUMNS` for a `SELECT`, not an `INSERT` -- pgvector's *text* output
#: rounds to seven significant digits, so reading `embedding` back as text
#: is lossy even though the stored float4 is exact. Casting to `real[]` hands
#: asyncpg a binary float4 array it decodes exactly, exactly as
#: `PgVectorStore.get` does; see that module's docstring for the round-trip
#: property that found the asymmetry.
_SELECT_COLUMNS = _COLUMNS.replace("embedding", "embedding::real[] AS embedding")

#: One definition of cosine similarity in this library, not two -- see
#: `redstring.vector.adapters.pgvector._SCORE`. `chunks` cannot import
#: `vector` (siblings under the same layer, and `lint-imports` forbids it),
#: so this is a second declaration proved identical to the first by
#: `tests/unit/chunks/test_postgres_schema.py`, not a shared import.
_SCORE = "1 - (embedding <=> $2::vector) / 2"


class PostgresChunkStore:
    """A `ChunkStore` backed by Postgres."""

    def __init__(
        self, pool: asyncpg.Pool[Any], *, table: str = "kg_chunks", dimension: int
    ) -> None:
        """Wrap an existing pool. `close()` will not close it.

        Ownership follows who created the pool, as on `PgVectorStore`: a
        caller that injected one keeps the right to close it, and `connect()`
        builds its own and does close it.

        `dimension` is required and keyword-only, matching `InMemoryChunkStore`
        and `PgVectorStore` -- declared at construction, not discovered from
        the first write; see the port's `SemanticCandidateSource.dimension`
        docstring for why an optional width was rejected.
        """
        if not _IDENTIFIER.fullmatch(table):
            raise ValueError(f"table must be a bare lowercase identifier, not {table!r}")
        if dimension <= 0:
            raise ValueError(f"dimension must be positive, not {dimension}")
        self._pool = pool
        self._table = table
        self._dimension = dimension
        self._owns_pool = False

    @classmethod
    async def connect(
        cls,
        dsn: str,
        *,
        table: str = "kg_chunks",
        dimension: int,
        # Passed straight to `asyncpg.create_pool`, whose own signature is
        # `**kwargs`; narrowing it here would mean restating asyncpg's options
        # and going stale against them.
        **pool_options: Any,  # noqa: ANN401
    ) -> Self:
        """Build a store owning a pool of its own, which `close()` closes."""
        try:
            import asyncpg
        except ImportError as error:  # pragma: no cover - needs asyncpg absent
            raise ImportError(
                "PostgresChunkStore.connect needs asyncpg: install "
                "`redstring[pgvector]`, the extra that carries it"
            ) from error

        pool = await asyncpg.create_pool(dsn, **pool_options)
        store = cls(pool, table=table, dimension=dimension)
        store._owns_pool = True
        return store

    async def close(self) -> None:
        """Release the pool, if this store created it."""
        if self._owns_pool:
            await self._pool.close()

    async def __aenter__(self) -> Self:
        """Enter a block whose exit closes this store. See `__aexit__`."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close on the way out, and **never suppress**.

        The `None` return is the decision, not an omission: `__aexit__` is
        read for truthiness, so any truthy value would swallow whatever the
        body raised -- including `CancelledError`, which would break task
        cancellation for the caller. `None` is falsy, so the exception
        propagates and this is a resource-release block rather than an
        exception handler.

        Closing goes through `close()`, so ownership still decides: a store
        wrapping an injected pool leaves it open here exactly as it does
        there.
        """
        await self.close()

    @property
    def table(self) -> str:
        return self._table

    @property
    def dimension(self) -> int:
        return self._dimension

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def ensure_schema(self) -> None:
        """Create the extension, table and indexes. Idempotent.

        `CREATE EXTENSION IF NOT EXISTS vector` runs first, as `PgVectorStore`
        does, because `embedding vector(n)` needs the type to exist before
        either the `CREATE TABLE` or the repair `ALTER`s below can run.

        Raises `DimensionMismatchError` if `embedding` already exists at a
        different declared width -- mirroring `PgVectorStore.ensure_schema`
        exactly, and for the same reason: `ADD COLUMN IF NOT EXISTS embedding
        vector(n)` is a no-op against a column that already exists, silently,
        regardless of whether `n` agrees with what is already there. Without
        this check a table carrying `embedding vector(384)` opened with
        `dimension=768` would pass `ensure_schema` clean and then fail every
        write at runtime with an opaque Postgres error, rather than failing
        once, loudly, at startup.
        """
        async with self._pool.acquire() as connection:
            await connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            for statement in self._schema_statements():
                await connection.execute(statement)
            declared = await connection.fetchval(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = $1::regclass AND attname = 'embedding' AND NOT attisdropped",
                self._table,
            )
        if declared is not None and declared != self._dimension:
            raise DimensionMismatchError(expected=int(declared), actual=self._dimension)

    def _schema_statements(self) -> tuple[str, ...]:
        """The DDL, as data, so a server-free test can read it.

        The primary key is the **pair**, because content addressing makes a
        collision on `id` alone ordinary rather than unthinkable: the same
        passage of the same source under two tenants hashes identically.

        The index covers `get_by_source`'s filter *and* its full ordering --
        `(tenant_id, source_id, chunk_index, id)` -- so the order is served by
        the index rather than by a sort. `id` is in it because the tie-break is
        part of the port's contract, and an index stopping at `chunk_index`
        would leave the tie resolved by whatever the plan happened to do.

        `chunk_index` is `integer`. See the module docstring: `text` here is a
        working store that reorders documents.

        `doc_length` is a column on this row rather than something computed
        on read, because `lexical_candidates` needs it for every candidate
        the term-index query returns and recomputing it there would mean
        re-tokenizing `text` in SQL -- exactly the divergence
        `domain/tokenize.py`'s module docstring exists to rule out. It is
        immutable per id for the same reason the term index is; see
        `_ON_CONFLICT`.

        `<table>_entity_ids_idx` is a GIN index over the array column,
        supporting `get_by_entity`'s `entity_ids @> ARRAY[$2::uuid]`. GIN's
        array operator class indexes `@>`, `<@`, `&&` and whole-array `=`;
        it does not index `scalar = ANY(col)`, which is why `get_by_entity`
        uses containment rather than the more obvious membership test.

        `<table>_terms` is the term index: one row per `(tenant_id, chunk_id,
        term)`, carrying that term's frequency in the chunk. **`ON DELETE
        CASCADE` is load-bearing, not incidental.** It is what lets
        `replace_source`'s orphan delete, `delete_by_source` and
        `delete_by_tenant` all keep the term index correct without any of
        them mentioning `<table>_terms` -- three delete paths that would each
        otherwise need a second statement, and each one edit away from
        forgetting it. `<table>_terms_term_idx` supports
        `lexical_candidates`'s per-term lookups (document frequency and
        candidate matching), both filtered on `(tenant_id, term)`.

        `embedding` is `vector(<dimension>)`, nullable -- `None` is the "not
        yet embedded" state `semantic_candidates` skips rather than scores.
        There is deliberately no index on it, matching `PgVectorStore`; see
        `docs/adr/0012-no-ann-index-in-a-multi-tenant-vector-store.md`.

        **The two `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements are
        the owed migration (B89).** `CREATE TABLE IF NOT EXISTS` adds nothing
        to a `kg_chunks` that predates the lexical or semantic work, so a
        table created before either column existed would otherwise have
        neither -- and every query naming `_COLUMNS` would fail against it.
        They run after the `CREATE TABLE`, so a fresh database is created
        with both columns already present and then no-op altered; only a
        pre-existing table is actually repaired. See
        `tests/integration/chunks/test_postgres_store.py::test_ensure_schema_repairs_a_table_created_without_the_new_columns`,
        the only test that proves an `ALTER` does anything at all -- run only
        against a table that already has the column, it is a statement never
        observed to do anything.
        """
        return (
            f"CREATE TABLE IF NOT EXISTS {self._table} ("
            "  tenant_id   uuid    NOT NULL,"
            "  id          text    NOT NULL,"
            "  source_id   text    NOT NULL,"
            "  text        text    NOT NULL,"
            "  chunk_index integer NOT NULL,"
            "  start_char  integer NOT NULL,"
            "  end_char    integer NOT NULL,"
            "  entity_ids  uuid[]  NOT NULL DEFAULT '{}',"
            "  metadata    jsonb   NOT NULL DEFAULT '{}'::jsonb,"
            "  doc_length  integer NOT NULL DEFAULT 0,"
            f"  embedding   vector({self._dimension}),"
            "  PRIMARY KEY (tenant_id, id)"
            ")",
            f"ALTER TABLE {self._table} "
            "ADD COLUMN IF NOT EXISTS doc_length integer NOT NULL DEFAULT 0",
            f"ALTER TABLE {self._table} "
            f"ADD COLUMN IF NOT EXISTS embedding vector({self._dimension})",
            f"CREATE INDEX IF NOT EXISTS {self._table}_tenant_source_idx "
            f"ON {self._table} (tenant_id, source_id, chunk_index, id)",
            f"CREATE INDEX IF NOT EXISTS {self._table}_entity_ids_idx "
            f"ON {self._table} USING gin (entity_ids)",
            f"CREATE TABLE IF NOT EXISTS {self._table}_terms ("
            "  tenant_id uuid    NOT NULL,"
            "  chunk_id  text    NOT NULL,"
            "  term      text    NOT NULL,"
            "  tf        integer NOT NULL,"
            "  PRIMARY KEY (tenant_id, chunk_id, term),"
            f"  FOREIGN KEY (tenant_id, chunk_id) REFERENCES {self._table} (tenant_id, id) "
            "    ON DELETE CASCADE"
            ")",
            f"CREATE INDEX IF NOT EXISTS {self._table}_terms_term_idx "
            f"ON {self._table}_terms (tenant_id, term)",
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def _reject_wrong_width(self, chunks: Sequence[StoredChunk]) -> None:
        """A stored `embedding` must have exactly `self._dimension` components.

        Unchecked, Postgres itself rejects a wrong-width `vector(n)` value at
        the statement (a `DataError` naming neither the store nor which chunk
        was wrong), while `InMemoryChunkStore` used to accept the write and
        fail later, from inside `semantic_candidates`, with a bare
        `ValueError` from `zip(..., strict=True)` -- a silent divergence
        between the two adapters at the exact write this method's caller is
        one statement away from executing. Checked here, client-side, both
        adapters now reject with the same `DimensionMismatchError` at the same
        point in the call, mirroring `PgVectorStore._check`; see
        `ports/chunk_store.py`'s `upsert_many` docstring for the port-level
        rule this enforces.
        """
        for chunk in chunks:
            if chunk.embedding is not None and len(chunk.embedding) != self._dimension:
                raise DimensionMismatchError(expected=self._dimension, actual=len(chunk.embedding))

    async def upsert_many(self, chunks: Sequence[StoredChunk]) -> None:
        # Validated against the raw argument, not the deduplicated rows --
        # matching `PgVectorStore.upsert_many`'s reasoning: collapsing first
        # would let a rejected record vanish because a later one happened to
        # replace its key, so the same call would raise here and succeed
        # in-memory. Width before zero-norm, matching `semantic_candidates`'s
        # own guard order.
        self._reject_wrong_width(chunks)
        reject_zero_norm(chunks)

        rows = deduplicate(chunks)
        if not rows:
            return
        await self._pool.execute(self._insert_sql(), encode(rows), encode_terms(rows))

    def _insert_sql(self) -> str:
        """One statement for the whole batch, not a loop.

        A document's chunking is thousands of rows and the port says so. The
        payload is one `jsonb` parameter rather than parallel arrays because
        `entity_ids` is a per-row array; see the module docstring.

        The term-index insert rides in a CTE alongside the chunk insert.
        `written` is unreferenced by the final statement and still runs --
        Postgres executes every data-modifying CTE regardless of whether its
        output is read, the same property `_replace_sql` relies on for
        `written` there.
        """
        return (
            f"WITH written AS ("  # nosec B608
            f"    INSERT INTO {self._table} ({_COLUMNS})"
            f"    SELECT {_COLUMNS} FROM jsonb_to_recordset($1::jsonb) AS {_INCOMING}"
            f"    {_ON_CONFLICT}"
            "    RETURNING 1"
            ")"
            f"INSERT INTO {self._table}_terms (tenant_id, chunk_id, term, tf)"
            f"SELECT tenant_id, chunk_id, term, tf FROM jsonb_to_recordset($2::jsonb) "
            f"AS {_TERMS_INCOMING} "
            f"{_TERMS_ON_CONFLICT}"
        )

    async def replace_source(
        self,
        source_id: SourceId,
        tenant_id: TenantId,
        chunks: Sequence[StoredChunk],
    ) -> int:
        # Before the statement, not inside it: a rejected replacement must not
        # be a partial one, and the obvious ordering -- delete the orphans,
        # then discover the batch is invalid -- empties the source and raises.
        reject_foreign_chunks(chunks, source_id, tenant_id)
        self._reject_wrong_width(chunks)
        reject_zero_norm(chunks)

        rows = deduplicate(chunks)
        removed = await self._pool.fetchval(
            self._replace_sql(), tenant_id, source_id, encode(rows), encode_terms(rows)
        )
        return int(removed)

    def _replace_sql(self) -> str:
        """The whole fold in one statement: delete the orphans, write the rest.

        An empty payload is not special-cased anywhere. `id <> ALL (SELECT id
        FROM incoming)` over an empty set is true of every row, so an empty
        chunking empties the source -- which is what the port says it means,
        and `if not chunks: return 0` is the guard that looks defensive and
        leaves the old passages readable forever.

        The count comes through the `removed` CTE rather than from asyncpg's
        `"DELETE n"` status string, matching `PgVectorStore.delete_by_tenant`:
        a stringly-typed answer to a numeric question is one release note away
        from changing shape. `written` and `terms_written` are unreferenced by
        the final `SELECT` and still run -- Postgres executes every
        data-modifying CTE.

        The orphan `DELETE` needs no matching statement against
        `<table>_terms`: `ON DELETE CASCADE` removes those rows as a
        consequence of the row in `<table>` going, which is the whole reason
        this stays one statement instead of gaining a second delete to keep in
        step with the first.

        `terms_written` writes `ON CONFLICT DO NOTHING` rather than `DO
        UPDATE`; see `_TERMS_ON_CONFLICT`. Nothing here needs to reconcile a
        term row against a stale one, because a chunk id's terms cannot
        change.
        """
        return (
            "WITH incoming AS ("  # nosec B608
            f"    SELECT * FROM jsonb_to_recordset($3::jsonb) AS {_INCOMING}"
            "), removed AS ("
            f"    DELETE FROM {self._table}"
            "     WHERE tenant_id = $1 AND source_id = $2"
            "       AND id <> ALL (SELECT id FROM incoming)"
            "    RETURNING 1"
            "), written AS ("
            f"    INSERT INTO {self._table} ({_COLUMNS})"
            f"    SELECT {_COLUMNS} FROM incoming "
            f"    {_ON_CONFLICT}"
            "    RETURNING 1"
            "), terms_written AS ("
            f"    INSERT INTO {self._table}_terms (tenant_id, chunk_id, term, tf)"
            f"    SELECT tenant_id, chunk_id, term, tf FROM jsonb_to_recordset($4::jsonb) "
            f"    AS {_TERMS_INCOMING}"
            f"    {_TERMS_ON_CONFLICT}"
            "    RETURNING 1"
            ") SELECT (SELECT count(*) FROM removed)"
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, chunk_id: ChunkId, tenant_id: TenantId) -> StoredChunk | None:
        row = await self._pool.fetchrow(
            f"SELECT {_SELECT_COLUMNS} FROM {self._table} "  # nosec B608
            "WHERE tenant_id = $1 AND id = $2",
            tenant_id,
            chunk_id,
        )
        return None if row is None else _chunk_from(row)

    async def get_by_source(self, source_id: SourceId, tenant_id: TenantId) -> list[StoredChunk]:
        rows = await self._pool.fetch(
            # `chunk_index` then `id`, the port's total order. The index leads
            # with the same four columns, so this is a range scan rather than
            # a sort -- and `chunk_index` being `integer` is what makes the
            # ordering numeric.
            f"SELECT {_SELECT_COLUMNS} FROM {self._table} "  # nosec B608
            "WHERE tenant_id = $1 AND source_id = $2 "
            "ORDER BY chunk_index ASC, id ASC",
            tenant_id,
            source_id,
        )
        return [_chunk_from(row) for row in rows]

    async def get_by_entity(self, entity_id: EntityId, tenant_id: TenantId) -> list[StoredChunk]:
        rows = await self._pool.fetch(
            # A total order -- source, then index, then id -- served by no
            # single index; see the port docstring for why all three are
            # required. The GIN index on `entity_ids` serves the filter, but
            # only because the predicate is `@>` (array containment): GIN's
            # array operator class indexes `@>`, `<@`, `&&` and whole-array
            # `=`, never `scalar = ANY(col)`, and Postgres performs no
            # transform between the two. `ARRAY[$2::uuid]` is a one-element
            # array so this is semantically identical to `$2 = ANY
            # (entity_ids)`; see `tests/integration/chunks/test_postgres_store.py`
            # for the plan assertion naming the index.
            f"SELECT {_SELECT_COLUMNS} FROM {self._table} "  # nosec B608
            "WHERE tenant_id = $1 AND entity_ids @> ARRAY[$2::uuid] "
            "ORDER BY source_id ASC, chunk_index ASC, id ASC",
            tenant_id,
            entity_id,
        )
        return [_chunk_from(row) for row in rows]

    async def lexical_candidates(
        self,
        terms: Sequence[str],
        tenant_id: TenantId,
        limit: int,
    ) -> LexicalCandidates:
        # Rejected before any query, matching the in-memory adapter: a
        # rejected call must not have counted a corpus.
        if limit < 0:
            raise ValueError(f"limit must not be negative, got {limit}")

        # Short-circuits before a round trip, not merely before a scan --
        # the in-memory adapter's equivalent guard only avoids the scan,
        # since building `tokenized` there is not a network cost. The port's
        # contract is stated in terms both readings satisfy: "without
        # touching the store."
        if not terms:
            return LexicalCandidates(
                stats=CorpusStats(n_docs=0, avg_doc_length=0.0, doc_frequencies={}), candidates=[]
            )

        # Sorted so the parameter array is deterministic across calls with
        # the same term set -- not required for correctness, but it keeps
        # the statement's bound values reproducible for anyone reading a
        # slow-query log.
        distinct_terms = sorted(set(terms))

        async with self._pool.acquire() as connection:
            corpus = await connection.fetchrow(
                f"SELECT count(*) AS n_docs, coalesce(avg(doc_length), 0) AS avg_len "  # nosec B608
                f"FROM {self._table} WHERE tenant_id = $1",
                tenant_id,
            )
            frequency_rows = await connection.fetch(
                f"SELECT term, count(*) AS df FROM {self._table}_terms "  # nosec B608
                "WHERE tenant_id = $1 AND term = ANY ($2) GROUP BY term",
                tenant_id,
                distinct_terms,
            )
            candidate_rows = await connection.fetch(
                self._candidates_sql(), tenant_id, distinct_terms, limit
            )

        # `GROUP BY` in the frequency query cannot produce a row for a term no
        # chunk contains, so every requested term is seeded at `0` first --
        # the port requires `doc_frequencies` to cover exactly `terms`, absent
        # keys are not permitted as "the term wasn't asked about" here.
        doc_frequencies = dict.fromkeys(distinct_terms, 0)
        for row in frequency_rows:
            doc_frequencies[row["term"]] = row["df"]

        stats = CorpusStats(
            n_docs=corpus["n_docs"],
            avg_doc_length=float(corpus["avg_len"]),
            doc_frequencies=doc_frequencies,
        )
        candidates = [
            LexicalCandidate(
                chunk=_chunk_from(row),
                doc_length=row["doc_length"],
                term_frequencies=json.loads(row["tfs"]),
            )
            for row in candidate_rows
        ]
        return LexicalCandidates(stats=stats, candidates=candidates)

    def _candidates_sql(self) -> str:
        """Which chunks match, truncated by `limit`, with their term counts.

        `matched` picks the surviving chunk ids first, ordered by the
        contract's tie-break -- number of distinct requested terms matched,
        descending, then `id` ascending -- and `LIMIT`s there, before the join
        against `<table>` ever runs. Joining first and limiting after would
        pull every matching row's full text across the wire only to discard
        most of it.

        `jsonb_object_agg(term, tf)` builds this candidate's term frequencies
        in one aggregate rather than a second query per chunk; zero-frequency
        terms the chunk does not contain are filled in by the caller from the
        requested list, since a term with no match has no row here to
        aggregate.
        """
        return (
            "WITH matched AS ("  # nosec B608
            f"    SELECT chunk_id, count(*) AS matched_terms, jsonb_object_agg(term, tf) AS tfs"
            f"    FROM {self._table}_terms"
            "     WHERE tenant_id = $1 AND term = ANY ($2)"
            "     GROUP BY chunk_id"
            "     ORDER BY matched_terms DESC, chunk_id ASC"
            "     LIMIT $3"
            ")"
            f"SELECT c.{_SELECT_COLUMNS}, m.tfs"
            "  FROM matched m"
            f" JOIN {self._table} c ON c.tenant_id = $1 AND c.id = m.chunk_id"
            " ORDER BY m.matched_terms DESC, m.chunk_id ASC"
        )

    async def semantic_candidates(
        self,
        vector: Sequence[float],
        tenant_id: TenantId,
        limit: int,
        *,
        min_score: float | None = None,
    ) -> list[SemanticCandidate]:
        # Same guard order as `InMemoryChunkStore` and `PgVectorStore._check`:
        # limit before width, width before zero-norm, so a rejected call
        # never reaches a query and a zero-norm check never runs against a
        # vector of the wrong shape.
        if limit < 0:
            raise ValueError(f"limit must not be negative, got {limit}")
        if len(vector) != self._dimension:
            raise DimensionMismatchError(expected=self._dimension, actual=len(vector))
        if has_zero_norm(vector):
            raise ValueError("a zero vector has no direction; cosine is undefined for it")
        if limit == 0:
            # `LIMIT 0` would answer correctly; not asking is cheaper, and the
            # port promises `[]` regardless of what the tenant holds -- the
            # same shape as `PgVectorStore.search`.
            return []

        rows = await self._pool.fetch(
            self._semantic_candidates_sql(), tenant_id, encode_vector(vector), min_score, limit
        )
        return [
            SemanticCandidate(chunk=_chunk_from(row), score=clamp_score(float(row["score"])))
            for row in rows
        ]

    def _semantic_candidates_sql(self) -> str:
        """Which chunks are nearest, truncated by `limit`, with their score.

        Follows `_candidates_sql`'s shape: `matched` picks the surviving ids
        first, ordered by the port's tie-break -- score descending, then `id`
        ascending -- and `LIMIT`s there, before the join against `<table>`
        pulls the full row across the wire.

        **The ORDER BY says `embedding <=> $2` ASC, which is that same order
        spelled so an index can serve it.** See the comment on the clause.

        `embedding IS NOT NULL` excludes unembedded chunks from being
        candidates at all, rather than scoring them -- the port's stated
        difference from a missing lexical match. `min_score` is applied in
        this `WHERE`, before `LIMIT`, matching `VectorStore.search` and the
        port's own docstring for this method.
        """
        # `matched` names its id column `chunk_id`, not `id` -- `_SELECT_COLUMNS`
        # only qualifies its *first* entry with the `c.` prefix given below
        # (the same shape `_candidates_sql` relies on for `tfs`), so an `id`
        # column on both sides of the join would resolve ambiguously rather
        # than raising at statement build time.
        return (
            "WITH matched AS ("  # nosec B608
            f"    SELECT id AS chunk_id, {_SCORE} AS score"
            f"    FROM {self._table}"
            "     WHERE tenant_id = $1 AND embedding IS NOT NULL"
            f"       AND ($3::float8 IS NULL OR {_SCORE} >= $3)"
            # Ordered by the raw distance ascending rather than by `score`
            # descending, though the two are the same order: `score` is
            # `1 - d/2`, monotonically decreasing in `d`. The difference is
            # that `embedding <=> $2` ASC is a form an `hnsw` or `ivfflat`
            # index can serve and `1 - (embedding <=> $2)/2` DESC is not, so
            # the rescaled form silently forces a sequential scan and a full
            # sort even when an index exists. Measured with a partial HNSW
            # index present on a 549,697-row tenant: the rescaled form left
            # `idx_scan` at 0 and the index unread.
            #
            # The `id` tie-break is kept and costs nothing: Postgres takes
            # the leading distance key from the index and resolves ties with
            # an Incremental Sort above it, so the port's total order still
            # holds.
            "     ORDER BY embedding <=> $2::vector ASC, id ASC"
            "     LIMIT $4"
            ")"
            f"SELECT c.{_SELECT_COLUMNS}, m.score"
            "  FROM matched m"
            f" JOIN {self._table} c ON c.tenant_id = $1 AND c.id = m.chunk_id"
            " ORDER BY m.score DESC, m.chunk_id ASC"
        )

    async def backfill_lexical_index(self) -> int:
        """Recompute `doc_length` and the term rows from stored `text`. Idempotent.

        B89's other half: a row written before the term index existed has
        `doc_length = 0` and no `<table>_terms` rows, so it ranks as an empty
        document -- present, but never a candidate for any term it actually
        contains. Both are recomputed with `domain.tokenize`, the same
        function the write path uses, which is what makes a backfilled row
        identical to a freshly-written one rather than a second, divergent
        notion of "the terms of this chunk".

        One read followed by one write statement for the whole table, not a
        loop -- the same reasoning `_insert_sql` states for a document's
        chunking. Returns the number of chunk rows touched; term rows are
        additional and not counted, since a re-run touches every chunk row
        again (a genuine `UPDATE`, even when the value does not change) while
        writing no new term rows at all (`ON CONFLICT DO NOTHING`).
        """
        rows = await self._pool.fetch(f"SELECT id, tenant_id, text FROM {self._table}")  # nosec B608
        if not rows:
            return 0

        doc_lengths = json.dumps(
            [
                {
                    "tenant_id": str(row["tenant_id"]),
                    "id": row["id"],
                    "doc_length": len(tokenize(row["text"])),
                }
                for row in rows
            ]
        )
        term_rows = json.dumps(
            [
                {"tenant_id": str(row["tenant_id"]), "chunk_id": row["id"], "term": term, "tf": tf}
                for row in rows
                for term, tf in Counter(tokenize(row["text"])).items()
            ]
        )
        touched = await self._pool.fetchval(self._backfill_sql(), doc_lengths, term_rows)
        return int(touched)

    def _backfill_sql(self) -> str:
        """One statement: repair `doc_length`, then the term rows it implies.

        `updated` and `terms_written` are unreferenced by the final `SELECT`
        and still run -- Postgres executes every data-modifying CTE, the same
        property `_insert_sql` and `_replace_sql` rely on for their own
        unreferenced CTEs. The count comes from `updated` rather than
        asyncpg's status string, matching every other counted write here.
        """
        return (
            "WITH incoming AS ("  # nosec B608
            "    SELECT * FROM jsonb_to_recordset($1::jsonb) "
            "    AS t(tenant_id uuid, id text, doc_length integer)"
            "), updated AS ("
            f"    UPDATE {self._table} c SET doc_length = incoming.doc_length"
            "     FROM incoming"
            "     WHERE c.tenant_id = incoming.tenant_id AND c.id = incoming.id"
            "    RETURNING 1"
            "), terms_written AS ("
            f"    INSERT INTO {self._table}_terms (tenant_id, chunk_id, term, tf)"
            f"    SELECT tenant_id, chunk_id, term, tf FROM jsonb_to_recordset($2::jsonb) "
            f"    AS {_TERMS_INCOMING}"
            f"    {_TERMS_ON_CONFLICT}"
            "    RETURNING 1"
            ") SELECT (SELECT count(*) FROM updated)"
        )

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    async def delete_by_source(self, source_id: SourceId, tenant_id: TenantId) -> int:
        removed = await self._pool.fetchval(
            f"WITH removed AS (DELETE FROM {self._table} "  # nosec B608
            "WHERE tenant_id = $1 AND source_id = $2 RETURNING 1) SELECT count(*) FROM removed",
            tenant_id,
            source_id,
        )
        return int(removed)

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        removed = await self._pool.fetchval(
            f"WITH removed AS (DELETE FROM {self._table} "  # nosec B608
            "WHERE tenant_id = $1 RETURNING 1) SELECT count(*) FROM removed",
            tenant_id,
        )
        return int(removed)


# ----------------------------------------------------------------------
# Encoding
#
# Module-level and pure, so the default commit gate can execute them without a
# server. Slice 4 learned that an integration-only adapter is invisible to the
# gate: a cosmic-ray mutant in its source passed the entire suite because not
# one line of it ran.
# ----------------------------------------------------------------------


def reject_zero_norm(chunks: Sequence[StoredChunk]) -> None:
    """Cosine is undefined at zero magnitude.

    A stored zero vector would force every later `semantic_candidates` call
    to choose between a silent NaN and a per-row skip that hides a caller's
    bug, so it is rejected here instead -- the same choice
    `InMemoryChunkStore._reject_zero_norm` and `PgVectorStore._check` already
    make for their own ports. Chunks with no embedding at all are unaffected;
    only a *stored* zero vector is a problem.
    """
    for chunk in chunks:
        if chunk.embedding is not None and has_zero_norm(chunk.embedding):
            raise ValueError(f"chunk {chunk.id!r} has a zero vector; cosine is undefined for it")


def deduplicate(chunks: Sequence[StoredChunk]) -> list[StoredChunk]:
    """Collapse repeated `(tenant_id, id)` keys, keeping the last.

    Required, not an optimisation: `ON CONFLICT DO UPDATE` raises "cannot
    affect row a second time" when one statement touches a row twice, and a
    re-delivered event arrives as exactly that -- two chunks sharing
    `(source_id, text)` share a content-addressed id. Keeping the *last* is
    the port's stated rule, and it is the same one that applies across calls.

    The key is the **pair**. Content addressing makes a collision on `id`
    alone ordinary: the same passage under two tenants hashes identically, and
    a key built from `id` would silently merge two tenants' rows.
    """
    return list({(chunk.tenant_id, chunk.id): chunk for chunk in chunks}.values())


def encode(chunks: Sequence[StoredChunk]) -> str:
    """Render a batch as the `jsonb` document `jsonb_to_recordset` unpacks.

    Uuids become strings because JSON has no uuid; Postgres casts them back
    through the `uuid` and `uuid[]` column types in `_INCOMING`. `metadata`
    is nested as an object rather than a string, so it arrives as `jsonb`
    without a second parse.

    `doc_length` travels with the row rather than being computed in SQL: it
    is `len(tokenize(chunk.text))`, and computing that any other way risks
    the in-memory adapter and this one disagreeing about what a token is --
    exactly the divergence `domain/tokenize.py` exists to prevent.

    `embedding` travels as pgvector's text input form -- see `encode_vector`
    below -- or `None`, which `jsonb_to_recordset` casts straight to a `NULL`
    `vector` column.
    """
    return json.dumps(
        [
            {
                "tenant_id": str(chunk.tenant_id),
                "id": chunk.id,
                "source_id": chunk.source_id,
                "text": chunk.text,
                "chunk_index": chunk.chunk_index,
                "start_char": chunk.start_char,
                "end_char": chunk.end_char,
                "entity_ids": [str(entity_id) for entity_id in chunk.entity_ids],
                "metadata": chunk.metadata,
                "doc_length": len(tokenize(chunk.text)),
                "embedding": None if chunk.embedding is None else encode_vector(chunk.embedding),
            }
            for chunk in chunks
        ]
    )


def encode_vector(vector: Sequence[float]) -> str:
    """Render a vector as pgvector's text input form, `[1,2,3]`.

    A duplicate of `redstring.vector.adapters.pgvector.encode_vector`, not an
    import of it: `chunks` and `vector` are siblings in the layered contract
    in `pyproject.toml` and forbidden from importing each other, so the two
    copies are proved identical the way `_SCORE` is -- by a test, in
    `tests/unit/chunks/test_postgres_schema.py` -- rather than by sharing code.
    See that function's docstring for why this is text rather than
    `pgvector.asyncpg.register_vector`, and why there is deliberately no
    matching `decode_vector`.
    """
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def encode_terms(chunks: Sequence[StoredChunk]) -> str:
    """Render each chunk's term index as the `jsonb` document `_TERMS_INCOMING`
    unpacks: one row per `(tenant_id, chunk_id, term)` carrying that term's
    frequency.

    Computed from `text` with the same `tokenize` the in-memory adapter
    scores against directly -- this table exists only because Postgres needs
    something to seek on, not as a second source of truth. It is written once
    per id and never updated; see `_TERMS_ON_CONFLICT`.
    """
    return json.dumps(
        [
            {
                "tenant_id": str(chunk.tenant_id),
                "chunk_id": chunk.id,
                "term": term,
                "tf": tf,
            }
            for chunk in chunks
            for term, tf in Counter(tokenize(chunk.text)).items()
        ]
    )


def _chunk_from(row: Any) -> StoredChunk:  # noqa: ANN401 - asyncpg.Record, untyped
    """Rebuild a `StoredChunk` from a row.

    Rebuilt rather than handed back, which is what makes this adapter the
    second implementation worth having: the in-memory store returns the object
    it was given, so a contract satisfied by identity there is satisfied only
    by equality here.
    """
    return StoredChunk(
        # `id` is not passed: it is computed from `(source_id, text)`, which
        # is the same value the column holds for any row this adapter wrote.
        # A legacy row whose stored id was not content-addressed therefore
        # comes back under its derived id -- see the ADR; that row could only
        # have been written before the id became underivable.
        tenant_id=row["tenant_id"],
        source_id=row["source_id"],
        text=row["text"],
        chunk_index=row["chunk_index"],
        start_char=row["start_char"],
        end_char=row["end_char"],
        entity_ids=list(row["entity_ids"]),
        # jsonb comes back as text; asyncpg does not decode it without a
        # registered codec, and registering one on a pool this store may not
        # own would change behaviour for every other user of that pool.
        metadata=json.loads(row["metadata"]),
        # `_SELECT_COLUMNS` casts this to `real[]` in every query that reaches
        # here, so this is a binary float4 array asyncpg decodes exactly --
        # never pgvector's lossy seven-significant-digit text output. `None`
        # for an unembedded chunk.
        embedding=None if row["embedding"] is None else list(row["embedding"]),
    )
