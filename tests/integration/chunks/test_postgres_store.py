"""The Postgres `ChunkStore` adapter against the shared compliance suite.

Everything asserted about behaviour lives in `redstring.testing.chunk_store`.
This module supplies a store and adds only what is specific to *this*
adapter -- schema, the column type the ordering depends on, and round-trip
cost.

Start the backend deliberately::

    docker compose -f docker-compose.test.yml up -d postgres
    uv run pytest -m integration tests/integration/chunks/ -v

`-m integration` is required: `addopts` excludes the marker so the commit gate
stays infra-free. And this suite needs its **own invocation**, separate from
`tests/unit/chunks/` -- two subclasses of one compliance class in one process
is BACKLOG B10m.

## Why each xdist worker gets its own table

Copied from `tests/integration/vector/test_pgvector_store.py`, and for the
reason recorded there: the Neo4j suite resets one shared database and loses 36
tests to it under `-n auto` (B10f). The table name carries
`PYTEST_XDIST_WORKER`, so a worker truncates only its own rows.

## Why the skip probe writes a row

A TCP connect proves the port answers, not that the server can serve; a
Postgres still starting accepts connections. The probe creates a temporary
table and round-trips a row through it. This repo has paid for the weaker
check once already (BACKLOG B12).

Unlike pgvector's probe there is no extension to create: this adapter needs
plain Postgres, which is the only thing it asks of a deployment.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest

from redstring.chunks.adapters.postgres import PostgresChunkStore
from redstring.domain.chunk import chunk_id
from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.tokenize import tokenize
from redstring.testing.chunk_store import ChunkStoreCompliance

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg

    from redstring.ports.chunk_store import ChunkStore

pytestmark = pytest.mark.integration

DSN = os.environ.get(
    "KG_TEST_POSTGRES_DSN", "postgresql://postgres:redstring@localhost:5434/redstring_test"
)

#: One table per xdist worker; see the module docstring. `gw0` and friends are
#: already valid bare identifiers, and the adapter rejects anything that is not.
TABLE = f"kg_chunks_test_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"


async def _probe() -> asyncpg.Pool[Any] | None:
    """A connected pool, or `None` if Postgres cannot serve a trivial write."""
    try:
        import asyncpg
    except ImportError:  # pragma: no cover - asyncpg is installed with the extra
        return None

    try:
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
    except Exception:
        return None
    if pool is None:  # pragma: no cover - defensive; create_pool raises instead
        return None
    try:
        async with pool.acquire() as connection:
            await connection.execute(
                "CREATE TEMP TABLE _kg_chunk_probe (id text) ON COMMIT PRESERVE ROWS"
            )
            await connection.execute("INSERT INTO _kg_chunk_probe VALUES ('ok')")
            if await connection.fetchval("SELECT id FROM _kg_chunk_probe") != "ok":
                raise RuntimeError("the probe row did not come back")
    except Exception:
        await pool.close()
        return None
    return pool


_schema_ready = False


@pytest.fixture
async def pool() -> AsyncIterator[asyncpg.Pool[Any]]:
    """A pool for one test, or skip it.

    Function-scoped because an asyncpg pool binds to the event loop that
    created it, and `asyncio_default_fixture_loop_scope` is `function`.
    """
    global _schema_ready

    connected = await _probe()
    if connected is None:
        pytest.skip(
            f"Postgres is not serving at {DSN}. Start it with "
            f"`docker compose -f docker-compose.test.yml up -d postgres`."
        )
    if not _schema_ready:
        await PostgresChunkStore(
            connected, table=TABLE, dimension=ChunkStoreCompliance.DIMENSION
        ).ensure_schema()
        _schema_ready = True
    try:
        yield connected
    finally:
        await connected.close()


async def _columns(pool: asyncpg.Pool[Any], table: str) -> list[tuple[str, str, str | None]]:
    """One table's column list, as `(name, type, default)` in ordinal order.

    The default is included because `entity_ids uuid[] NOT NULL DEFAULT '{}'`
    and the same column without a default are two different schemas that carry
    the same name and type.
    """
    rows = await pool.fetch(
        "SELECT column_name, data_type, column_default FROM information_schema.columns "
        "WHERE table_name = $1 ORDER BY ordinal_position",
        table,
    )
    return [(row["column_name"], row["data_type"], row["column_default"]) for row in rows]


async def _truncate(pool: asyncpg.Pool[Any]) -> None:
    """Empty this worker's table.

    The reset lives here, not on the adapter: "delete every tenant's rows" is
    a test affordance, and a production `ChunkStore` should not offer one.
    `delete_by_tenant` is the port's bulk removal.

    `CASCADE` is required, not cosmetic: the `<table>_terms` table's foreign
    key makes a plain `TRUNCATE {TABLE}` raise `FeatureNotSupportedError`
    rather than silently leaving stale term rows behind, and cascading
    truncates the child table along with the parent -- which is what "empty"
    is supposed to mean now that a chunk row has a dependent table.
    """
    await pool.execute(f"TRUNCATE {TABLE} CASCADE")


class _OneConnectionPool:
    """Answers `.acquire()` with the one connection it wraps.

    `PostgresChunkStore.lexical_candidates` acquires its own connection
    internally, so a test that needs `SET` to survive into that query cannot
    hand the adapter a bare `Connection` (no `.acquire()`) or a real `Pool`
    (may hand back any connection, not the one carrying the `SET`). This is
    the minimum needed to satisfy the adapter's `Pool[Any]` usage --
    `acquire()` as an async context manager yielding the wrapped connection,
    and nothing else, because `lexical_candidates` is the only method this
    test exercises through it.
    """

    def __init__(
        self, connection: asyncpg.pool.PoolConnectionProxy[Any] | asyncpg.Connection[Any]
    ) -> None:
        self._connection = connection

    def acquire(self) -> _OneConnectionPool:
        return self

    async def __aenter__(self) -> Any:
        return self._connection

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        # `upsert_many` calls `self._pool.execute(...)` directly, without
        # going through `acquire()` -- proxy straight to the connection for
        # anything this class does not define itself.
        return getattr(self._connection, name)


class _RecordingPool:
    """Wraps one connection and remembers the last `.fetch()` sent through it.

    `get_by_entity` calls `self._pool.fetch(query, *args)` directly, without
    `.acquire()`, so this needs only `fetch` and `.acquire()` proxied through
    to the wrapped connection -- `__getattr__` covers anything else the
    adapter might reach for. The point is to EXPLAIN the *real* statement
    text a method sent, not a hand-copied string that can drift from it; see
    `test_get_by_entity_uses_the_gin_index`.
    """

    def __init__(
        self, connection: asyncpg.pool.PoolConnectionProxy[Any] | asyncpg.Connection[Any]
    ) -> None:
        self._connection = connection
        self.last_query: str | None = None
        self.last_args: tuple[Any, ...] = ()

    def acquire(self) -> _RecordingPool:
        return self

    async def __aenter__(self) -> Any:
        return self._connection

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def fetch(self, query: str, *args: Any) -> Any:
        self.last_query = query
        self.last_args = args
        return await self._connection.fetch(query, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


class TestPostgresChunkStore(ChunkStoreCompliance):
    """The whole compliance suite, unchanged, against real Postgres."""

    @pytest.fixture(autouse=True)
    def _pool(self, pool: asyncpg.Pool[Any]) -> None:
        # Stashed on the instance because `new_store` takes no arguments: the
        # suite's contract is that an adapter supplies exactly one thing.
        self._shared_pool = pool

    async def new_store(self) -> ChunkStore:
        await _truncate(self._shared_pool)
        return PostgresChunkStore(self._shared_pool, table=TABLE, dimension=self.DIMENSION)

    async def dispose(self, store: ChunkStore) -> None:
        assert isinstance(store, PostgresChunkStore)
        await store.close()


class TestPostgresChunkStoreSpecifics:
    """Behaviour the port does not specify, so the compliance suite cannot."""

    @pytest.fixture
    async def store(self, pool: asyncpg.Pool[Any]) -> AsyncIterator[PostgresChunkStore]:
        await _truncate(pool)
        built = PostgresChunkStore(pool, table=TABLE, dimension=ChunkStoreCompliance.DIMENSION)
        try:
            yield built
        finally:
            await built.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def test_ensure_schema_is_idempotent(self, store: PostgresChunkStore) -> None:
        await store.ensure_schema()
        await store.ensure_schema()

    async def test_ensure_schema_rejects_a_declared_width_disagreement(
        self, pool: asyncpg.Pool[Any]
    ) -> None:
        """A table already carrying `embedding vector(n)` at a different `n`
        must fail loudly at `ensure_schema`, not silently at the first write.

        `ADD COLUMN IF NOT EXISTS embedding vector(n)` is a no-op against a
        column that already exists, regardless of whether the declared `n`
        agrees with what is already there -- so without this check, a table
        built at one width and opened at another would pass `ensure_schema`
        clean and then fail every write at runtime with an opaque Postgres
        error. Mirrors `PgVectorStore`'s own version of this test.
        """
        table = f"{TABLE}_width_mismatch"
        await pool.execute(f"DROP TABLE IF EXISTS {table}_terms CASCADE")
        await pool.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

        narrow = PostgresChunkStore(pool, table=table, dimension=4)
        await narrow.ensure_schema()

        wide = PostgresChunkStore(pool, table=table, dimension=8)
        with pytest.raises(DimensionMismatchError) as raised:
            await wide.ensure_schema()
        assert raised.value.expected == 4
        assert raised.value.actual == 8

        await pool.execute(f"DROP TABLE {table}_terms CASCADE")
        await pool.execute(f"DROP TABLE {table} CASCADE")

    async def test_ensure_schema_creates_the_table_from_nothing(
        self, pool: asyncpg.Pool[Any]
    ) -> None:
        """The DDL must actually run.

        Every other test here works against a table an earlier run created, so
        **the schema statements could do nothing at all and nothing would
        notice** -- cosmic-ray proved that against the pgvector adapter by
        replacing the loop's iterable with `[]`. The only test that can see it
        is one starting from no table.
        """
        table = f"{TABLE}_fresh"
        terms_table = f"{table}_terms"
        # `DROP TABLE {table} CASCADE` only drops *constraints* that depend on
        # `table` -- `terms_table`'s foreign key onto it -- not `terms_table`
        # itself, which is a separate table Postgres does not consider owned
        # by `table`. A leftover `_fresh_terms` from an earlier aborted run
        # therefore survives a plain `DROP TABLE {table} CASCADE` and fails
        # the assertion below; both tables are dropped explicitly so this
        # test is idempotent regardless of how a previous run ended.
        await pool.execute(f"DROP TABLE IF EXISTS {terms_table} CASCADE")
        await pool.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        assert not await pool.fetchval("SELECT to_regclass($1) IS NOT NULL", table)
        assert not await pool.fetchval("SELECT to_regclass($1) IS NOT NULL", terms_table)

        fresh = PostgresChunkStore(pool, table=table, dimension=ChunkStoreCompliance.DIMENSION)
        await fresh.ensure_schema()

        assert await pool.fetchval("SELECT to_regclass($1) IS NOT NULL", table)
        assert await pool.fetchval("SELECT to_regclass($1) IS NOT NULL", terms_table)
        # Usable, not merely present.
        tenant = uuid4()
        written = ChunkStoreCompliance._chunk(tenant, "doc-1", "a passage", chunk_index=0)
        await fresh.upsert_many([written])
        assert await fresh.get(written.id, tenant) == written

        # And the table the other tests use matches what the adapter
        # *currently* declares. This half is about the harness, not the DDL,
        # and without it the module has a standing false-green: `_schema_ready`
        # is a module-level global and the DDL is `CREATE TABLE IF NOT
        # EXISTS`, so a change to the column list or a column type has no
        # effect on any run until someone drops the worker table by hand. A
        # sabotage touching only the DDL would leave the whole suite green and
        # the schema change invisible.
        #
        # Both tables are compared -- not just the chunk table -- because the
        # `_terms` table is a second schema this adapter declares and the same
        # staleness would hide it just as well: an old worker table predating
        # the term index would leave every `lexical_candidates` test running
        # against a table that silently does not exist.
        #
        # Compared rather than silently repaired with a `DROP TABLE`: dropping
        # would make a stale table a thing that quietly heals, and the failure
        # a developer needs to see is "your table predates this schema" --
        # which is also the machine-versus-CI disagreement this catches.
        assert await _columns(pool, table) == await _columns(pool, TABLE), (
            f"{TABLE} does not match the schema {type(fresh).__name__} now "
            f"declares. `CREATE TABLE IF NOT EXISTS` cannot migrate it, so "
            f"every other test in this module is running against a stale "
            f"table. Drop it: DROP TABLE {TABLE} CASCADE;"
        )
        assert await _columns(pool, terms_table) == await _columns(pool, f"{TABLE}_terms"), (
            f"{TABLE}_terms does not match the schema {type(fresh).__name__} now "
            f"declares. `CREATE TABLE IF NOT EXISTS` cannot migrate it, so "
            f"every other test in this module is running against a stale "
            f"table. Drop it: DROP TABLE {TABLE}_terms;"
        )

        await pool.execute(f"DROP TABLE {terms_table} CASCADE")
        await pool.execute(f"DROP TABLE {table} CASCADE")

    async def test_ensure_schema_repairs_a_table_created_without_the_new_columns(
        self, pool: asyncpg.Pool[Any]
    ) -> None:
        """The ALTER is proved against a table that actually lacks the columns.

        `ADD COLUMN IF NOT EXISTS` run only against a table that already has
        the column is a statement never observed to do anything -- this is
        the test that observes it. It builds the pre-migration table by hand
        (the pre-`doc_length`, pre-`embedding` column set B89 describes),
        runs `ensure_schema`, and asserts the columns arrive and a query
        naming `_COLUMNS` then succeeds.
        """
        table = f"{TABLE}_premigration"
        # `IF EXISTS ... CASCADE` on the *table* only drops its own
        # constraints, not a sibling `_terms` table that references it -- see
        # `test_ensure_schema_creates_the_table_from_nothing`'s docstring for
        # the same trap. Both are dropped explicitly so an aborted previous
        # run cannot leave a stale `_terms` table behind for the next one.
        await pool.execute(f"DROP TABLE IF EXISTS {table}_terms CASCADE")
        await pool.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        # The schema this adapter had before the lexical or semantic work --
        # no `doc_length`, no `embedding`, no `_terms` table at all.
        await pool.execute(
            f"CREATE TABLE {table} ("
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
            ")"
        )
        tenant = uuid4()
        await pool.execute(
            f"INSERT INTO {table} "
            "(tenant_id, id, source_id, text, chunk_index, start_char, end_char) "
            "VALUES ($1, 'pre-1', 'doc-1', 'a passage written before the migration', 0, 0, 39)",
            tenant,
        )
        columns_before = {name for name, _, _ in await _columns(pool, table)}
        assert "doc_length" not in columns_before
        assert "embedding" not in columns_before

        store = PostgresChunkStore(pool, table=table, dimension=ChunkStoreCompliance.DIMENSION)
        await store.ensure_schema()

        columns_after = {name for name, _, _ in await _columns(pool, table)}
        assert "doc_length" in columns_after
        assert "embedding" in columns_after
        # A query naming `_COLUMNS` -- every read the adapter issues -- now
        # succeeds against the repaired table, rather than raising
        # `UndefinedColumnError`.
        found = await store.get("pre-1", tenant)
        assert found is not None
        assert found.embedding is None
        # `doc_length` is not a `StoredChunk` field -- it is postgres-only
        # storage the adapter reads back for `lexical_candidates` -- so it is
        # checked with a direct query, matching the `ADD COLUMN ... DEFAULT 0`
        # the ALTER declares.
        assert await pool.fetchval(f"SELECT doc_length FROM {table} WHERE id = 'pre-1'") == 0

        await pool.execute(f"DROP TABLE {table}_terms CASCADE")
        await pool.execute(f"DROP TABLE {table} CASCADE")

    async def test_backfill_lexical_index_makes_a_pre_migration_row_rankable(
        self, pool: asyncpg.Pool[Any]
    ) -> None:
        """A backfill asserted only by its return count is a counter, not a repair.

        A row written directly, bypassing `upsert_many`, has `doc_length = 0`
        and no term-index rows -- the state a pre-migration row would be left
        in by `ensure_schema` alone. `lexical_candidates` must rank it as
        having **no** matches, since nothing in `<table>_terms` mentions it,
        even though its text plainly contains the term. `backfill_lexical_index`
        is what makes it findable, and this asserts the ranking is wrong
        before and right after -- not merely that some rows were touched.
        """
        table = f"{TABLE}_backfill"
        await pool.execute(f"DROP TABLE IF EXISTS {table}_terms CASCADE")
        await pool.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        store = PostgresChunkStore(pool, table=table, dimension=ChunkStoreCompliance.DIMENSION)
        await store.ensure_schema()

        tenant = uuid4()
        chunk = ChunkStoreCompliance._chunk(tenant, "doc-1", "alpha appears in this passage")
        await pool.execute(
            f"INSERT INTO {table} "
            "(tenant_id, id, source_id, text, chunk_index, start_char, end_char) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            tenant,
            chunk.id,
            str(chunk.source_id),
            chunk.text,
            chunk.chunk_index,
            chunk.start_char,
            chunk.end_char,
        )

        before = await store.lexical_candidates(["alpha"], tenant, 10)
        assert before.candidates == []
        assert before.stats.doc_frequencies["alpha"] == 0

        touched = await store.backfill_lexical_index()
        assert touched == 1

        after = await store.lexical_candidates(["alpha"], tenant, 10)
        assert [candidate.chunk.id for candidate in after.candidates] == [chunk.id]
        assert after.stats.doc_frequencies["alpha"] == 1
        assert after.candidates[0].doc_length == len(tokenize(chunk.text))

        # Idempotent: running it again touches the same row and changes nothing.
        touched_again = await store.backfill_lexical_index()
        assert touched_again == 1
        again = await store.lexical_candidates(["alpha"], tenant, 10)
        assert [candidate.chunk.id for candidate in again.candidates] == [chunk.id]

        await pool.execute(f"DROP TABLE {table}_terms CASCADE")
        await pool.execute(f"DROP TABLE {table} CASCADE")

    async def test_chunk_index_is_an_integer_column(
        self, store: PostgresChunkStore, pool: asyncpg.Pool[Any]
    ) -> None:
        """The schema mistake the compliance suite can only see through data.

        A `text` column stores, reads and round-trips every chunk correctly and
        returns chunk 10 before chunk 2. The suite catches it with an index-10
        case; this asserts the cause directly, so a failure names the column
        rather than an out-of-order list.
        """
        await store.ensure_schema()
        declared = await pool.fetchval(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = 'chunk_index'",
            TABLE,
        )
        assert declared == "integer"

    async def test_the_primary_key_is_the_pair_not_the_id(
        self, store: PostgresChunkStore, pool: asyncpg.Pool[Any]
    ) -> None:
        """Content addressing makes an `id`-only key a live defect.

        The same passage of the same source under two tenants hashes
        identically, so a key on `id` alone would make one tenant's write
        replace another's row.
        """
        await store.ensure_schema()
        columns = await pool.fetch(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = $1::regclass AND i.indisprimary "
            "ORDER BY array_position(i.indkey, a.attnum)",
            TABLE,
        )
        assert [row["attname"] for row in columns] == ["tenant_id", "id"]

    async def test_get_by_source_seeks_rather_than_scanning_the_table(
        self, store: PostgresChunkStore, pool: asyncpg.Pool[Any]
    ) -> None:
        """The results are identical either way, so only the plan can see this.

        Slice 4's tenant-scoped Neo4j reads planned as a full scan across every
        tenant while returning the right answers. The dataset is large enough
        (12k rows over 300 tenants) that the planner has a real choice.

        **Deliberately not asserted: the absence of a `Sort` node.** The index
        covers the full ordering and *can* be walked in order, but with forty
        matching rows the planner prefers a bitmap scan and an in-memory sort,
        which is the cheaper plan and an equally correct one. Pinning the
        ordered index scan would make this test a statement about the row
        count rather than about the schema -- the same over-specification the
        pgvector plan test records having already been bitten by. What the
        index must do is turn the *filter* into a seek; the ordering is a
        contract the compliance suite checks against results.
        """
        tenants = [uuid4() for _ in range(300)]
        await store.upsert_many(
            [
                ChunkStoreCompliance._chunk(
                    tenant, f"doc-{index % 4}", f"passage {tenant} {index}", chunk_index=index
                )
                for tenant in tenants
                for index in range(40)
            ]
        )
        await pool.execute(f"ANALYZE {TABLE}")

        rows = await pool.fetch(
            "EXPLAIN (ANALYZE false, COSTS false, VERBOSE true) "
            f"SELECT * FROM {TABLE} WHERE tenant_id = $1 AND source_id = $2 "
            "ORDER BY chunk_index ASC, id ASC",
            tenants[0],
            "doc-1",
        )
        plan = "\n".join(row["QUERY PLAN"] for row in rows)

        assert "Seq Scan" not in plan, (
            f"a tenant-scoped read of one source reads every row of every tenant:\n{plan}"
        )
        conditions = [line for line in plan.splitlines() if "Index Cond" in line]
        assert conditions, f"no index condition at all:\n{plan}"
        assert "tenant_id" in conditions[0]
        assert "source_id" in conditions[0]

    async def test_get_by_entity_uses_the_gin_index(self, pool: asyncpg.Pool[Any]) -> None:
        """`get_by_entity` must plan as a GIN index seek, not a tenant scan.

        GIN's array operator class indexes `@>`, `<@`, `&&` and whole-array
        `=` -- it does not index `scalar = ANY(col)`, and Postgres performs
        no transform between the two. A predicate written as `$2 = ANY
        (entity_ids)` therefore plans as `Bitmap Index Scan` on the *primary
        key* with the array test pushed into `Filter`, reading every row of
        the tenant regardless of how selective the entity filter is --
        verified against this project's own container before this test
        existed: 20 000 rows, `enable_seqscan = off`, `Filter: ($2 = ANY
        (entity_ids))` with no `entity_ids_idx` anywhere in the plan.
        `entity_ids @> ARRAY[$2::uuid]` plans as a bitmap scan *on the GIN
        index*, with the containment as `Index Cond`.

        This EXPLAINs the adapter's **own statement text**, captured off the
        real `fetch()` call rather than retyped by hand -- BACKLOG B93 records
        that the sibling truncation-tie-break test EXPLAINs a hand-reconstructed
        proxy of the adapter's SQL, which can drift from what the adapter
        actually sends without this test noticing. `_RecordingPool` sits
        between the adapter and the connection for exactly that reason: it
        never rewrites the query, it only remembers the string and parameters
        `PostgresChunkStore.get_by_entity` passed to `.fetch()`, and replays
        the same text under `EXPLAIN`.
        """
        async with pool.acquire() as connection:
            await connection.execute(f"TRUNCATE {TABLE} CASCADE")
            await connection.execute("SET enable_seqscan = off")
            recording = _RecordingPool(connection)
            store = PostgresChunkStore(
                cast("asyncpg.Pool[Any]", recording),
                table=TABLE,
                dimension=ChunkStoreCompliance.DIMENSION,
            )

            tenant = uuid4()
            target_entity = uuid4()
            matching = ChunkStoreCompliance._chunk(
                tenant, "doc-1", "the matching passage", entity_ids=[target_entity]
            )
            await store.upsert_many(
                [
                    matching,
                    *(
                        ChunkStoreCompliance._chunk(
                            tenant,
                            "doc-1",
                            f"other passage {i}",
                            chunk_index=i + 1,
                            entity_ids=[uuid4()],
                        )
                        for i in range(500)
                    ),
                ]
            )
            await connection.execute(f"ANALYZE {TABLE}")

            found = await store.get_by_entity(target_entity, tenant)
            assert recording.last_query is not None, "get_by_entity never called .fetch()"

            plan = "\n".join(
                row["QUERY PLAN"]
                for row in await connection.fetch(
                    f"EXPLAIN (ANALYZE false, COSTS false) {recording.last_query}",
                    *recording.last_args,
                )
            )

        assert f"{TABLE}_entity_ids_idx" in plan, (
            f"the GIN index is not in the plan at all:\n{plan}"
        )
        conditions = [line for line in plan.splitlines() if "Index Cond" in line]
        assert conditions, f"no index condition -- the filter is not seeking:\n{plan}"
        assert "entity_ids" in conditions[0], f"not on entity_ids:\n{plan}"
        assert "@>" in conditions[0], f"the index condition is not the containment test:\n{plan}"
        assert [chunk.id for chunk in found] == [matching.id]

    async def test_the_order_by_alone_produces_the_tie_break(self, pool: asyncpg.Pool[Any]) -> None:
        """The total order is guaranteed **twice**, so neither guarantee is
        falsifiable by any other test in this repository.

        `get_by_source` says `ORDER BY chunk_index ASC, id ASC` *and* the
        covering index `(tenant_id, source_id, chunk_index, id)` supplies that
        order to the planner. Deleting `, id ASC` leaves all 54 tests green,
        including the compliance tie-break case -- and truncating the index to
        `(tenant_id, source_id, chunk_index)` with the clause shortened makes
        that case fail, which is what proves the survivor is not equivalent.
        Whichever mechanism a future author removes, the suite stays green on
        the other.

        So this one takes the index away. With `enable_indexscan` and
        `enable_bitmapscan` off the planner must sequentially scan, and the
        only thing that can produce the order is the clause. The plan is
        asserted too: without that, a version of this test where the settings
        silently failed to apply would be the original unfalsifiable test
        again, wearing a longer docstring.

        The input is the compliance suite's tie-break shape -- two chunks
        sharing `chunk_index=3`, and a third at index 0 holding the id that
        sorts *last*, so `(chunk_index, id)` and `id` alone disagree.
        """
        # A single connection, because `SET` is per-session and a pool hands
        # out whichever connection is free. The adapter only reaches for
        # `fetch`/`fetchrow`/`fetchval`/`execute` on the paths used here, all
        # of which a Connection has with the same signatures.
        async with pool.acquire() as connection:
            await connection.execute(f"TRUNCATE {TABLE} CASCADE")
            await connection.execute("SET enable_indexscan = off")
            await connection.execute("SET enable_bitmapscan = off")
            store = PostgresChunkStore(
                cast("asyncpg.Pool[Any]", connection),
                table=TABLE,
                dimension=ChunkStoreCompliance.DIMENSION,
            )

            tenant = uuid4()
            low_tie = ChunkStoreCompliance._chunk(tenant, "doc-1", "passage gamma", chunk_index=3)
            high_tie = ChunkStoreCompliance._chunk(tenant, "doc-1", "passage alpha", chunk_index=3)
            leader = ChunkStoreCompliance._chunk(tenant, "doc-1", "passage beta", chunk_index=0)
            assert low_tie.id < high_tie.id < leader.id
            await store.upsert_many([high_tie, low_tie, leader])

            plan = "\n".join(
                row["QUERY PLAN"]
                for row in await connection.fetch(
                    "EXPLAIN (ANALYZE false, COSTS false) "
                    f"SELECT * FROM {TABLE} WHERE tenant_id = $1 AND source_id = $2 "
                    "ORDER BY chunk_index ASC, id ASC",
                    tenant,
                    "doc-1",
                )
            )
            assert "Seq Scan" in plan, (
                f"the index is still serving this query, so the ORDER BY is "
                f"still unobservable:\n{plan}"
            )
            assert "Sort" in plan, f"nothing is sorting, so nothing is ordering:\n{plan}"

            found = await store.get_by_source("doc-1", tenant)

        assert [chunk.id for chunk in found] == [leader.id, low_tie.id, high_tie.id]

    async def test_lexical_candidates_truncation_tie_break_is_unfalsifiable_by_plan_alone(
        self, pool: asyncpg.Pool[Any]
    ) -> None:
        """The `matched` CTE's `, chunk_id ASC` is the same trap as above, in
        the term-index query.

        Under the planner's default plan, `GROUP BY chunk_id` is satisfied by
        a `GroupAggregate` over a sorted input, and that sort happens to leave
        ties in `chunk_id` order even though only `ORDER BY matched_terms
        DESC` names it -- deleting `, chunk_id ASC` from the clause leaves
        every compliance case green. `enable_indexscan` /
        `enable_bitmapscan = off` is not enough here, unlike the `get_by_source`
        case above: without an index the planner still reaches a
        `GroupAggregate` over a `Sort`, which happens to reproduce the same
        order by accident of the grouping strategy. Disabling
        `enable_presorted_aggregate` alone still leaves a `GroupAggregate`
        over an explicit `Sort` on `chunk_id` -- on a two-row table the
        planner prefers sorting over hashing regardless. `enable_sort = off`
        is what actually forces a `HashAggregate`, whose bucket order carries
        no relationship to `chunk_id` -- the plan is asserted for exactly
        this reason, so a future settings change that stops applying cannot
        pass silently.

        Unlike the `get_by_source` test above, `lexical_candidates` acquires
        its own connection internally (three queries in one round trip, see
        the module docstring), so it cannot be handed a bare `Connection` in
        place of the pool the way `get_by_source` can -- it needs something
        that answers `.acquire()`. `_OneConnectionPool` below is that: it
        wraps the one connection this test configured with `SET` and hands
        that same connection back from `acquire()`, so `lexical_candidates`'s
        three queries run on the session the settings apply to rather than on
        whatever connection a real pool happens to have free.
        """
        async with pool.acquire() as connection:
            await connection.execute("SET enable_indexscan = off")
            await connection.execute("SET enable_bitmapscan = off")
            await connection.execute("SET enable_presorted_aggregate = off")
            await connection.execute("SET enable_sort = off")
            await connection.execute(f"TRUNCATE {TABLE} CASCADE")
            store = PostgresChunkStore(
                cast("asyncpg.Pool[Any]", _OneConnectionPool(connection)),
                table=TABLE,
                dimension=ChunkStoreCompliance.DIMENSION,
            )

            tenant = uuid4()
            low_tie = ChunkStoreCompliance._chunk(tenant, "doc-1", "common alpha", chunk_index=3)
            high_tie = ChunkStoreCompliance._chunk(tenant, "doc-1", "common beta", chunk_index=3)
            assert low_tie.id < high_tie.id
            await store.upsert_many([low_tie, high_tie])

            plan = "\n".join(
                row["QUERY PLAN"]
                for row in await connection.fetch(
                    "EXPLAIN (ANALYZE false, COSTS false) "
                    f"SELECT chunk_id FROM {TABLE}_terms "
                    "WHERE tenant_id = $1 AND term = ANY ($2) "
                    "GROUP BY chunk_id ORDER BY count(*) DESC LIMIT 1",
                    tenant,
                    ["common"],
                )
            )
            assert "HashAggregate" in plan, (
                f"a GroupAggregate over a sorted input can reproduce chunk_id "
                f"order by accident, making the tie-break unfalsifiable:\n{plan}"
            )

            result = await store.lexical_candidates(["common"], tenant, 1)

        assert {candidate.chunk.id for candidate in result.candidates} == {low_tie.id}

    # ------------------------------------------------------------------
    # Round-trip cost
    #
    # `upsert_many` and `replace_source` are each one statement by contract --
    # the port says a chunking is thousands of rows and that the fold must be
    # atomic. That is a property of this adapter, so the compliance suite
    # cannot assert it, and it is the exact regression a refactor introduces
    # silently.
    # ------------------------------------------------------------------

    async def test_upsert_many_is_one_statement(
        self, store: PostgresChunkStore, pool: asyncpg.Pool[Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tenant = uuid4()
        chunks = [
            ChunkStoreCompliance._chunk(tenant, "doc-1", f"passage {i}", chunk_index=i)
            for i in range(250)
        ]
        executed: list[str] = []
        original = type(pool).execute

        async def counting(self: Any, query: str, *args: Any, **kwargs: Any) -> Any:
            executed.append(query)
            return await original(self, query, *args, **kwargs)

        monkeypatch.setattr(type(pool), "execute", counting)

        await store.upsert_many(chunks)

        assert len(executed) == 1, f"{len(executed)} statements for 250 rows: this is a loop"
        assert len(await store.get_by_source("doc-1", tenant)) == 250

    async def test_replace_source_is_one_statement(
        self, store: PostgresChunkStore, pool: asyncpg.Pool[Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two round trips would leave a crash between them mid-fold.

        `fetchval` *and* `execute` are counted: an implementation that deleted
        through one and wrote through the other would pass a check that only
        watched one of them.
        """
        tenant = uuid4()
        old = [
            ChunkStoreCompliance._chunk(tenant, "doc-1", f"old {i}", chunk_index=i)
            for i in range(50)
        ]
        await store.upsert_many(old)
        fresh = [
            ChunkStoreCompliance._chunk(tenant, "doc-1", f"new {i}", chunk_index=i)
            for i in range(50)
        ]

        statements: list[str] = []
        for name in ("execute", "fetchval", "fetch", "fetchrow"):
            original = getattr(type(pool), name)

            def counting(self: Any, query: str, *args: Any, _inner: Any = original) -> Any:
                statements.append(query)
                return _inner(self, query, *args)

            monkeypatch.setattr(type(pool), name, counting)

        removed = await store.replace_source("doc-1", tenant, fresh)

        assert removed == 50
        assert len(statements) == 1, f"replace_source ran {len(statements)} statements"

    # ------------------------------------------------------------------
    # Encoding fidelity
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "metadata",
        [
            pytest.param({}, id="empty"),
            pytest.param({"nested": {"deep": {"deeper": [1, 2, {"x": None}]}}}, id="nested"),
            pytest.param({"empty_dict": {}, "empty_list": []}, id="empty-containers"),
            pytest.param({"mixed": [1, "two", None, True, {"k": []}]}, id="heterogeneous-array"),
            # jsonb keeps `true` and `1` apart; a coercion through int would not.
            pytest.param({"bool": True, "int": 1}, id="bool-is-not-int"),
            pytest.param({"big": 2**70}, id="bignum"),
            pytest.param({"": "empty key", "unicode": "é中\U0001f600"}, id="odd-keys"),
        ],
    )
    async def test_metadata_round_trips_exactly(
        self, store: PostgresChunkStore, metadata: dict[str, Any]
    ) -> None:
        tenant = uuid4()
        written = ChunkStoreCompliance._chunk(
            tenant, "doc-1", "a passage", chunk_index=0, metadata=metadata
        )
        await store.upsert_many([written])

        found = await store.get(written.id, tenant)
        assert found is not None
        assert found.metadata == metadata

    async def test_many_entity_ids_of_differing_lengths_survive_one_batch(
        self, store: PostgresChunkStore
    ) -> None:
        """The binding decision, asserted rather than argued.

        `entity_ids` is a `uuid[]` per row, so parallel-array binding would
        need a nested array -- and Postgres arrays are rectangular, so asyncpg
        rejects unequal-length sub-arrays outright. Chunks carry different
        numbers of entities by construction, which is why the payload is one
        `jsonb` document. A batch whose rows have 0, 1 and 3 entities is the
        input that would have failed.
        """
        tenant = uuid4()
        counts = [0, 1, 3, 2]
        chunks = [
            ChunkStoreCompliance._chunk(
                tenant,
                "doc-1",
                f"passage {i}",
                chunk_index=i,
                entity_ids=[uuid4() for _ in range(count)],
            )
            for i, count in enumerate(counts)
        ]

        await store.upsert_many(chunks)

        found = await store.get_by_source("doc-1", tenant)
        assert [len(chunk.entity_ids) for chunk in found] == counts
        assert found == chunks

    # ------------------------------------------------------------------
    # Table names and connection ownership
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "table",
        ['kg_chunks"; DROP TABLE users; --', "public.kg_chunks", "KgChunks", "", "1_chunks"],
    )
    async def test_a_table_name_that_is_not_a_bare_identifier_is_rejected(
        self, pool: asyncpg.Pool[Any], table: str
    ) -> None:
        """What the `# nosec B608` markers rest on.

        The table name is interpolated because Postgres has no parameter form
        for an identifier, so the guard -- not the marker -- is what makes the
        interpolation safe.
        """
        with pytest.raises(ValueError, match="bare lowercase identifier"):
            PostgresChunkStore(pool, table=table, dimension=ChunkStoreCompliance.DIMENSION)

    async def test_close_does_not_close_a_pool_it_does_not_own(
        self, store: PostgresChunkStore, pool: asyncpg.Pool[Any]
    ) -> None:
        await store.close()
        assert await pool.fetchval("SELECT 1") == 1

    async def test_connect_owns_and_closes_its_pool(self, pool: asyncpg.Pool[Any]) -> None:
        """`pool` is requested purely for its skip.

        This is the only test that builds a pool of its own, so without the
        dependency it is the only one that *fails* rather than skips when
        Postgres is absent. A skip guard is only honest if every test in the
        module is behind it.
        """
        owned = await PostgresChunkStore.connect(
            DSN, table=TABLE, dimension=ChunkStoreCompliance.DIMENSION
        )
        await owned.ensure_schema()
        assert await owned.get(chunk_id("doc-1", "never stored"), uuid4()) is None
        await owned.close()

        with pytest.raises(Exception, match="closed"):
            await owned.get(chunk_id("doc-1", "never stored"), uuid4())
