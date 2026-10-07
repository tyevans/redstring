"""Postgres `ChunkStore`: the second adapter, and the test of the port.

Modelled on `redstring.vector.adapters.pgvector` -- asyncpg directly with no
ORM, a guarded import naming the extra to install, an interpolated table name
proved to be a bare identifier first, and delete counts taken through a CTE
rather than by parsing asyncpg's `"DELETE n"` status string.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Self

from redstring.chunks.provenance import reject_foreign_chunks
from redstring.domain.chunk import StoredChunk
from redstring.domain.exceptions import DimensionMismatchError

from .postgres_encode import deduplicate, encode, encode_terms, reject_zero_norm
from .postgres_search import backfill_lexical, search_lexical, search_semantic
from .postgres_sql import (
    _IDENTIFIER,
    _SELECT_COLUMNS,
    backfill_sql,
    candidates_sql,
    insert_sql,
    replace_sql,
    schema_statements,
    semantic_candidates_sql,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    import asyncpg

    from redstring.domain.chunk import ChunkId
    from redstring.domain.chunk_ranking import LexicalCandidates
    from redstring.domain.chunk_retrieval import SemanticCandidate
    from redstring.domain.ids import EntityId, SourceId, TenantId


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
        """Create the extension, table and indexes. Idempotent."""
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
        """The DDL, as data, so a server-free test can read it."""
        return schema_statements(self._table, self._dimension)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def _reject_wrong_width(self, chunks: Sequence[StoredChunk]) -> None:
        """A stored `embedding` must have exactly `self._dimension` components."""
        for chunk in chunks:
            if chunk.embedding is not None and len(chunk.embedding) != self._dimension:
                raise DimensionMismatchError(expected=self._dimension, actual=len(chunk.embedding))

    async def upsert_many(self, chunks: Sequence[StoredChunk]) -> int:
        self._reject_wrong_width(chunks)
        reject_zero_norm(chunks)

        rows = deduplicate(chunks)
        if not rows:
            return 0
        added = await self._pool.fetchval(self._insert_sql(), encode(rows), encode_terms(rows))
        return int(added)

    def _insert_sql(self) -> str:
        """One statement for the whole batch, not a loop; returns rows added."""
        return insert_sql(self._table)

    async def replace_source(
        self,
        source_id: SourceId,
        tenant_id: TenantId,
        chunks: Sequence[StoredChunk],
    ) -> int:
        reject_foreign_chunks(chunks, source_id, tenant_id)
        self._reject_wrong_width(chunks)
        reject_zero_norm(chunks)

        rows = deduplicate(chunks)
        removed = await self._pool.fetchval(
            self._replace_sql(), tenant_id, source_id, encode(rows), encode_terms(rows)
        )
        return int(removed)

    def _replace_sql(self) -> str:
        """The whole fold in one statement: delete the orphans, write the rest."""
        return replace_sql(self._table)

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

    async def existing_ids(self, chunk_ids: Sequence[ChunkId], tenant_id: TenantId) -> set[ChunkId]:
        """Which of `chunk_ids` this tenant holds, as an index seek."""
        if not chunk_ids:
            return set()
        rows = await self._pool.fetch(
            f"SELECT id FROM {self._table} "  # nosec B608
            "WHERE tenant_id = $1 AND id = ANY($2::text[])",
            tenant_id,
            list(chunk_ids),
        )
        return {row["id"] for row in rows}

    async def get_by_source(self, source_id: SourceId, tenant_id: TenantId) -> list[StoredChunk]:
        rows = await self._pool.fetch(
            f"SELECT {_SELECT_COLUMNS} FROM {self._table} "  # nosec B608
            "WHERE tenant_id = $1 AND source_id = $2 "
            "ORDER BY chunk_index ASC, id ASC",
            tenant_id,
            source_id,
        )
        return [_chunk_from(row) for row in rows]

    async def get_by_entity(self, entity_id: EntityId, tenant_id: TenantId) -> list[StoredChunk]:
        rows = await self._pool.fetch(
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
        return await search_lexical(self._pool, self._table, terms, tenant_id, limit, _chunk_from)

    def _candidates_sql(self) -> str:
        """Which chunks match, truncated by `limit`, with their term counts."""
        return candidates_sql(self._table)

    async def semantic_candidates(
        self,
        vector: Sequence[float],
        tenant_id: TenantId,
        limit: int,
        *,
        min_score: float | None = None,
    ) -> list[SemanticCandidate]:
        return await search_semantic(
            self._pool,
            self._table,
            vector,
            tenant_id,
            limit,
            self._dimension,
            _chunk_from,
            min_score=min_score,
        )

    def _semantic_candidates_sql(self) -> str:
        """Which chunks are nearest, truncated by `limit`, with their score."""
        return semantic_candidates_sql(self._table)

    async def backfill_lexical_index(self) -> int:
        """Recompute `doc_length` and the term rows from stored `text`. Idempotent."""
        return await backfill_lexical(self._pool, self._table)

    def _backfill_sql(self) -> str:
        """One statement: repair `doc_length`, then the term rows it implies."""
        return backfill_sql(self._table)

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
