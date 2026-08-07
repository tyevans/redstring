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
from typing import TYPE_CHECKING, Any, Self

from redstring.chunks.provenance import reject_foreign_chunks
from redstring.domain.chunk import StoredChunk

if TYPE_CHECKING:
    from collections.abc import Sequence

    import asyncpg

    from redstring.domain.chunk import ChunkId
    from redstring.domain.ids import SourceId, TenantId

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
_INCOMING = (
    "t(tenant_id uuid, id text, source_id text, text text, chunk_index integer, "
    "start_char integer, end_char integer, entity_ids uuid[], metadata jsonb)"
)

#: What every write sets when the key already exists. Last-write-wins, and the
#: list is the whole row bar the key -- an omitted column here is a field one
#: adapter preserves and the other drops, which is `recurring-defects.md` §1's
#: third observed shape verbatim.
_ON_CONFLICT = (
    "ON CONFLICT (tenant_id, id) DO UPDATE SET "
    "source_id = EXCLUDED.source_id, text = EXCLUDED.text, "
    "chunk_index = EXCLUDED.chunk_index, start_char = EXCLUDED.start_char, "
    "end_char = EXCLUDED.end_char, entity_ids = EXCLUDED.entity_ids, "
    "metadata = EXCLUDED.metadata"
)

_COLUMNS = "tenant_id, id, source_id, text, chunk_index, start_char, end_char, entity_ids, metadata"


class PostgresChunkStore:
    """A `ChunkStore` backed by Postgres."""

    def __init__(self, pool: asyncpg.Pool[Any], *, table: str = "kg_chunks") -> None:
        """Wrap an existing pool. `close()` will not close it.

        Ownership follows who created the pool, as on `PgVectorStore`: a
        caller that injected one keeps the right to close it, and `connect()`
        builds its own and does close it.
        """
        if not _IDENTIFIER.fullmatch(table):
            raise ValueError(f"table must be a bare lowercase identifier, not {table!r}")
        self._pool = pool
        self._table = table
        self._owns_pool = False

    @classmethod
    async def connect(
        cls,
        dsn: str,
        *,
        table: str = "kg_chunks",
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
        store = cls(pool, table=table)
        store._owns_pool = True
        return store

    async def close(self) -> None:
        """Release the pool, if this store created it."""
        if self._owns_pool:
            await self._pool.close()

    @property
    def table(self) -> str:
        return self._table

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def ensure_schema(self) -> None:
        """Create the table and its index. Idempotent."""
        async with self._pool.acquire() as connection:
            for statement in self._schema_statements():
                await connection.execute(statement)

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
            "  PRIMARY KEY (tenant_id, id)"
            ")",
            f"CREATE INDEX IF NOT EXISTS {self._table}_tenant_source_idx "
            f"ON {self._table} (tenant_id, source_id, chunk_index, id)",
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def upsert_many(self, chunks: Sequence[StoredChunk]) -> None:
        rows = deduplicate(chunks)
        if not rows:
            return
        await self._pool.execute(self._insert_sql(), encode(rows))

    def _insert_sql(self) -> str:
        """One statement for the whole batch, not a loop.

        A document's chunking is thousands of rows and the port says so. The
        payload is one `jsonb` parameter rather than parallel arrays because
        `entity_ids` is a per-row array; see the module docstring.
        """
        return (
            f"INSERT INTO {self._table} ({_COLUMNS}) "  # nosec B608
            f"SELECT {_COLUMNS} FROM jsonb_to_recordset($1::jsonb) AS {_INCOMING} "
            f"{_ON_CONFLICT}"
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

        removed = await self._pool.fetchval(
            self._replace_sql(), tenant_id, source_id, encode(deduplicate(chunks))
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
        from changing shape. `written` is unreferenced by the final `SELECT`
        and still runs -- Postgres executes every data-modifying CTE.
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
            ") SELECT (SELECT count(*) FROM removed)"
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, chunk_id: ChunkId, tenant_id: TenantId) -> StoredChunk | None:
        row = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM {self._table} WHERE tenant_id = $1 AND id = $2",  # nosec B608
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
            f"SELECT {_COLUMNS} FROM {self._table} "  # nosec B608
            "WHERE tenant_id = $1 AND source_id = $2 "
            "ORDER BY chunk_index ASC, id ASC",
            tenant_id,
            source_id,
        )
        return [_chunk_from(row) for row in rows]

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
            }
            for chunk in chunks
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
        id=row["id"],
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
    )
